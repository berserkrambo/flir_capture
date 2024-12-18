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


class FlirCamera(threading.Thread):
    def __init__(self, camera, exposure, gain, capture_mode):
        super().__init__()
        print("init")
        self.cam = camera

        self.terminate = threading.Event()
        self.capture_mode = capture_mode

        # self.queue = queue
        self.exposure = exposure
        self.gain = gain
        self.width = None
        self.height = None
        self.last_frame = None
        self.cam_id = None

    def get_camera_resolution(self):
        """
        Ottiene i parametri di larghezza (Width) e altezza (Height) della camera direttamente.
        """
        try:
            # Ottenere la mappa dei nodi della camera
            nodemap = self.cam.GetNodeMap()

            # Ottenere i nodi Width e Height
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

    def load_defaults(self):
        self.cam.UserSetSelector.SetValue(PySpin.UserSetSelector_Default)
        self.cam.UserSetLoad.Execute()
        self.setup_camera(self.exposure, self.gain)
        self.cam_id = self.cam.DeviceSerialNumber.ToString()


    def set_trigger_hw(self):

        self.load_defaults()

        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_Off)
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Line0)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)
        # self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_RisingEdge)
        self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_FallingEdge)
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)

        self.capture_mode = "trigger_hw"


    def set_trigger_sw(self):

        self.load_defaults()

        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_Off)
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Software)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)

        self.capture_mode = "trigger_sw"


    def set_continuous(self):

        self.load_defaults()
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_Off)

        self.capture_mode = "continuous"


    def get_frame(self):
        if self.capture_mode == "trigger_sw":
            self.cam.TriggerSoftware.Execute()

        frame = self.cam.GetNextImage()
        status = not frame.IsIncomplete()
        if status:
            self.last_frame = frame.GetNDArray()
        else:
            self.last_frame = None
        frame.Release()

        if self.capture_mode != "continuous":
            print(self.cam_id, status)

        return self.cam_id, self.last_frame

    def run(self):
        self.cam.Init()
        self.width, self.height = self.get_camera_resolution()
        print(self.cam_id, self.width, self.height)

        if self.capture_mode == "continuous":
            self.set_continuous()
        elif self.capture_mode == "trigger_sw":
            self.set_trigger_sw()
        elif self.capture_mode == "trigger_hw":
            self.set_trigger_hw()

        self.cam.BeginAcquisition()
        self.terminate.wait()

        self.cam.EndAcquisition()
        self.cam.DeInit()

    def release(self):
        self.terminate.set()

    def stop(self):
        self.release()

    def __del__(self):
        self.release()

class FlirWrapper():
    def __init__(self, exposure, gain, capture_mode):
        assert capture_mode in ["trigger_hw", "trigger_sw", "continuous"]
        self.capture_mode = capture_mode
        self.system = PySpin.System.GetInstance()

        camera_list = self.system.GetCameras()
        self.caps = [FlirCamera(cam, exposure, gain, self.capture_mode) for i, cam in enumerate(camera_list)]

        [c.start() for c in self.caps]
        print("cam started, sleeping 2 sec to be ready...")
        time.sleep(2)

        print("Now ready...")

    def get_frames(self):
        return [c.get_frame() for c in self.caps]

    def release(self):
        [c.release() for c in self.caps]
        [c.join() for c in self.caps]
        self.system.ReleaseInstance()

    def stop(self):
        self.release()

    def __del__(self):
        self.release()


def trigger_capture(exposure, gain, capture_mode, use_socket):
    assert capture_mode in ["trigger_hw", "trigger_sw"]
    cams = FlirWrapper(exposure=exposure, gain=gain, capture_mode=capture_mode)

    if use_socket:
        # Crea una socket TCP/IP
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Associa la socket all'indirizzo IP e alla porta
        server_address = ('localhost', 12345)
        server_socket.bind(server_address)
        # Ascolta le connessioni in arrivo (massimo 1 client)
        server_socket.listen(1)
        print("Server in ascolto su porta 12345...")

        while True:
            # Accetta una connessione
            connection, client_address = server_socket.accept()
            try:
                print(f"Connessione accettata da {client_address}")
                # Riceve i dati dal client
                data = connection.recv(1024).decode()  # Il messaggio inviato dal client

                if data.strip().lower() == "cattura":
                    print("Ricevuto comando 'cattura', eseguo la funzione...")

                    data = cams.get_frames()
                    data_out = {}
                    for cam_id, frame in data:
                        # Codifica l'immagine come PNG in memoria
                        if frame is not None:
                            cv2.imwrite(Path(".") / f"{cam_id}.png", frame)
                            _, buffer = cv2.imencode('.png', frame)
                            data_out[cam_id] = buffer.tobytes()
                        else:
                            data_out[cam_id] = None

                    data_out = pickle.dumps(data_out)

                    # Invia i dati serializzati al client
                    connection.sendall(data_out)
                else:
                    print(f"Comando sconosciuto: {data}")
                    connection.sendall(b"Comando sconosciuto.\n")
            finally:
                # Chiude la connessione
                connection.close()

    else:
        data = cams.get_frames()
        data_out = {}
        for cam_id, frame in data:
            # Codifica l'immagine come PNG in memoria
            if frame is not None:
                cv2.imwrite(Path(".") / f"{cam_id}.png", frame)
                _, buffer = cv2.imencode('.png', frame)
                data_out[cam_id] = buffer.tobytes()
            else:
                data_out[cam_id] = None

    return pickle.dumps(data_out)


if __name__ == '__main__':
    use_socket = False
    trigger = 'trigger_hw'  # trigger_hw - trigger_sw
    exposure = 55
    gain = 10

    trigger_capture(exposure=exposure, gain=gain, capture_mode=trigger, use_socket=use_socket)
    # echo -n "cattura" | nc localhost 12345
