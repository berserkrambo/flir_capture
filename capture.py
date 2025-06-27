import time
import PySpin
import threading
from threading import Lock
import copy
from path import Path
import cv2
import numpy as np
import socket
import pickle

from flir_utils import print_device_info


class FlirCamera(threading.Thread):
    def __init__(self, camera, exposure, gain, capture_mode):
        super().__init__()
        self.cam = camera

        self.capture_mode = capture_mode
        self.terminate = threading.Event()

        # Aggiungiamo un Lock per garantire l'accesso thread-safe all'ultimo frame
        self.lock = Lock()

        self.exposure = exposure
        self.gain = gain
        self.width = None
        self.height = None

        # Inizializziamo last_frame a None
        self.last_frame = None
        self.cam_id = None
        self.nodemap = None

        # ImageProcessor per la conversione del formato colore
        self.image_processor = PySpin.ImageProcessor()
        # Imposta il formato di output desiderato, BGR8 è comodo per OpenCV
        self.image_processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)

    def get_camera_resolution(self):
        """
        Ottiene i parametri di larghezza (Width) e altezza (Height) della camera direttamente.
        """
        try:
            nodemap = self.cam.GetNodeMap()
            width_node = PySpin.CIntegerPtr(nodemap.GetNode("Width"))
            height_node = PySpin.CIntegerPtr(nodemap.GetNode("Height"))

            if PySpin.IsAvailable(width_node) and PySpin.IsReadable(width_node) and \
                    PySpin.IsAvailable(height_node) and PySpin.IsReadable(height_node):
                width = width_node.GetValue()
                height = height_node.GetValue()
                print(f"Camera Resolution: Width = {width}, Height = {height}")
                return width, height
            else:
                print("Width or Height node not available/readable.")
                return None, None
        except PySpin.SpinnakerException as ex:
            print(f"Error: {ex}")
            return None, None

    def setup_camera(self, exposure, gain):
        # Set auto white balance to off
        # self.cam.BalanceWhiteAuto.SetValue(PySpin.BalanceWhiteAuto_Off)

        # Turn off auto exposure
        self.cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
        # Set exposure mode to "Timed"
        self.cam.ExposureMode.SetValue(PySpin.ExposureMode_Timed)
        # Set exposure time to 'exposure' microseconds
        self.cam.ExposureTime.SetValue(exposure)

        # Turn off auto gain
        self.cam.GainAuto.SetValue(PySpin.GainAuto_Off)
        # Set gain to 'gain' dB
        self.cam.Gain.SetValue(gain)

        # Imposta il formato pixel su uno comune, come BayerRG8 per le camere a colori
        # Se le tue camere sono monocromatiche, potresti usare Mono8
        if self.cam.PixelFormat.GetAccessMode() == PySpin.RW:
            self.cam.PixelFormat.SetValue(PySpin.PixelFormat_BayerRG8)

    def load_defaults(self):
        self.cam.UserSetSelector.SetValue(PySpin.UserSetSelector_Default)
        self.cam.UserSetLoad.Execute()
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_Off)
        self.setup_camera(self.exposure, self.gain)
        self.cam_id = self.cam.DeviceSerialNumber.ToString()

    def set_trigger_hw(self):
        self.load_defaults()
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Line0)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)
        self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_FallingEdge)
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)
        self.capture_mode = "trigger_hw"

    def set_trigger_sw(self):
        self.load_defaults()
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Software)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)
        self.capture_mode = "trigger_sw"

    # NUOVO METODO PER L'ACQUISIZIONE "GATED"
    def set_trigger_gated(self):
        """
        Configura la camera per acquisire continuamente frame
        finché il segnale di trigger HW (Line0) è ALTO.
        """
        self.load_defaults()
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Line0)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)

        # La chiave è questa impostazione: la camera è attiva quando il livello è alto
        self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_LevelHigh)

        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)
        self.capture_mode = "trigger_gated"

    def set_continuous(self):
        self.load_defaults()
        self.capture_mode = "continuous"

    def get_frame(self):
        """
        Metodo per il thread principale per ottenere l'ultimo frame catturato.
        Restituisce una COPIA dell'ultimo frame per evitare race conditions.
        """
        with self.lock:
            # Usiamo deepcopy per essere sicuri che il thread principale lavori
            # su un'immagine che non verrà modificata dal thread di acquisizione.
            last_frame_copy = copy.deepcopy(self.last_frame)

        return self.cam_id, last_frame_copy

    def run(self):
        """
        Questo è il cuore del thread. Esegue la configurazione
        e poi entra in un loop di acquisizione finché non viene terminato.
        """
        nodemap_tldevice = self.cam.GetTLDeviceNodeMap()
        print_device_info(nodemap_tldevice)

        self.cam.Init()
        self.width, self.height = self.get_camera_resolution()

        # Seleziona la modalità di cattura
        if self.capture_mode == "continuous":
            self.set_continuous()
        elif self.capture_mode == "trigger_sw":
            self.set_trigger_sw()
        elif self.capture_mode == "trigger_hw":
            self.set_trigger_hw()
        elif self.capture_mode == "trigger_gated":  # NUOVA MODALITÀ
            self.set_trigger_gated()

        print(f"Camera {self.cam_id} started in '{self.capture_mode}' mode.")

        # L'acquisizione è sempre "Continuous" a livello di SDK,
        # ma il trigger ne governa il comportamento.
        self.cam.AcquisitionMode.SetValue(PySpin.AcquisitionMode_Continuous)
        self.cam.BeginAcquisition()

        # Loop di acquisizione (il "produttore")
        while not self.terminate.is_set():
            try:
                # GetNextImage attenderà un'immagine. Il timeout è importante.
                # Se il trigger non arriva (es. in modalità gated con segnale basso),
                # andrà in timeout, l'eccezione verrà gestita e il loop continuerà.
                frame = self.cam.GetNextImage(1000)  # Timeout di 1 secondo

                if not frame.IsIncomplete():
                    # Converte l'immagine in un formato che OpenCV può usare (BGR8)
                    converted_image = self.image_processor.Convert(frame, PySpin.PixelFormat_BGR8)

                    # Ottieni i dati come array numpy
                    image_data = converted_image.GetData()

                    # Aggiorna l'ultimo frame in modo thread-safe
                    with self.lock:
                        self.last_frame = image_data.reshape((self.height, self.width, 3))

                # Rilascia il buffer dell'immagine
                frame.Release()

            except PySpin.SpinnakerException as ex:
                # Se GetNextImage va in timeout, è normale in modalità trigger.
                # In questo caso, non facciamo nulla e il loop riprova.
                if 'SPINNAKER_ERR_TIMEOUT' in str(ex):
                    # print(f"Camera {self.cam_id}: Timeout waiting for image.")
                    pass
                else:
                    print(f"Camera {self.cam_id}: Spinnaker error in run loop: {ex}")
                    break

        # Cleanup
        self.cam.EndAcquisition()
        self.load_defaults()
        self.cam.DeInit()
        print(f"Camera {self.cam_id} thread finished.")

    def release(self):
        self.terminate.set()

    def stop(self):
        self.release()

    def __del__(self):
        # La release esplicita è sempre preferibile a __del__
        self.release()


class FlirWrapper():
    def __init__(self, exposure, gain, capture_mode):
        # Aggiungiamo la nuova modalità all'elenco di quelle valide
        assert capture_mode in ["trigger_hw", "trigger_sw", "continuous", "trigger_gated"]
        self.capture_mode = capture_mode
        self.system = PySpin.System.GetInstance()

        self.camera_list = self.system.GetCameras()
        if not self.camera_list.GetSize():
            print("Nessuna camera trovata. Assicurati che siano collegate e che i driver siano installati.")
            self.caps = []
            return

        self.caps = [FlirCamera(cam, exposure, gain, self.capture_mode) for cam in self.camera_list]

        [c.start() for c in self.caps]
        print("Camere in fase di avvio, attesa di 2 secondi per la stabilizzazione...")
        time.sleep(2)

        print("Wrapper pronto.")

    def get_frames(self):
        return [c.get_frame() for c in self.caps]

    def release(self):
        if hasattr(self, 'caps'):
            [c.release() for c in self.caps]
            [c.join() for c in self.caps]  # Attende la fine dei thread
            del self.caps
        if hasattr(self, 'camera_list'):
            self.camera_list.Clear()
        if hasattr(self, 'system'):
            self.system.ReleaseInstance()
        print("FlirWrapper released.")

    def stop(self):
        self.release()

    def __del__(self):
        self.release()


def main(exposure, gain, capture_mode):
    assert capture_mode in ["trigger_hw", "trigger_sw", "continuous", "trigger_gated"]

    cams = FlirWrapper(exposure=exposure, gain=gain, capture_mode=capture_mode)

    if not cams.caps:
        print("Uscita dal programma perché non sono state trovate telecamere.")
        return

    try:
        while True:
            t0 = time.time()
            all_frames_data = cams.get_frames()

            # Mostra i frame da tutte le camere
            for cam_id, frame in all_frames_data:
                if frame is not None:
                    # Rinomina la finestra per evitare conflitti se ci sono più camere
                    window_name = f"Camera {cam_id}"
                    cv2.imshow(window_name, frame)
                else:
                    # In modalità trigger, potrebbe non esserci un frame se non è scattato
                    if capture_mode in ["trigger_gated", "trigger_hw", "trigger_sw"]:
                        pass  # È normale
                    else:
                        print(f"Camera {cam_id}: Frame is None.")

            # Per la modalità continua o gated, un piccolo waitKey è necessario per aggiornare le finestre
            k = cv2.waitKey(1)

            if k == 27 or k == ord('q'):
                break

            t1 = time.time()
            delta_t = t1 - t0
            if delta_t > 0:
                print(f"\rFPS loop principale: {int(1 / delta_t):03d}", end="")

    finally:
        # Assicurati che le risorse vengano rilasciate correttamente anche in caso di errore
        print("\nChiusura in corso...")
        cams.release()
        cv2.destroyAllWindows()
        print("Programma terminato.")


if __name__ == '__main__':
    # SELEZIONA QUI LA MODALITÀ DI ACQUISIZIONE

    # 1. Acquisizione continua standard
    # capture_mode = 'continuous'

    # 2. Un frame per ogni trigger software (dovresti implementare la chiamata al trigger)
    # capture_mode = 'trigger_sw'

    # 3. Un frame per ogni impulso di trigger hardware
    # capture_mode = 'trigger_hw'

    # 4. NUOVA MODALITÀ: Acquisisce continuamente finché il trigger HW è alto
    capture_mode = 'trigger_gated'

    exposure = 800  # Esempio di valore comune (8 ms)
    gain = 10


    main(exposure=exposure, gain=gain, capture_mode=capture_mode)