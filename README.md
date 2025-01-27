# Flir Capture

Questo repository fornisce un wrapper per l'acquisizione di immagini utilizzando telecamere FLIR tramite il framework PySpin. Supporta tre modalità di acquisizione: **continuous**, **trigger hardware (trigger_hw)** e **trigger software (trigger_sw)**. 

Il progetto è progettato per semplificare l'uso delle telecamere FLIR e integra funzionalità avanzate come la configurazione automatica della telecamera, la gestione di thread multipli e l'acquisizione di immagini in tempo reale.

---

## Caratteristiche principali

- **Gestione delle modalità di acquisizione**: 
  - Modalità continua
  - Modalità trigger hardware
  - Modalità trigger software
- **Multithreading**: Ogni telecamera è gestita da un thread separato.
- **Configurazione personalizzabile**: Esposizione, guadagno e modalità di acquisizione configurabili.
- **Visualizzazione in tempo reale**: Utilizzo di OpenCV per visualizzare i frame acquisiti.

---

## Requisiti

- Python 3.7 o superiore
- Librerie necessarie:
  - `PySpin`
  - `opencv-python`
  - `numpy`
  - `path`
  - `socket`
  - `pickle`
- Un sistema compatibile con le telecamere FLIR e il framework Spinnaker SDK.

---

## Installazione

1. Clonare il repository:
    ```bash
    git clone https://github.com/tuo-utente/flir-camera-wrapper.git
    cd flir-camera-wrapper
    ```
2. Installare le dipendenze:
    ```bash
    pip install -r requirements.txt
    ```
3. Verificare che il framework Spinnaker SDK sia correttamente installato sul sistema.

---

## Esempio di utilizzo

### Configurazione di base

Un esempio di utilizzo del wrapper per avviare l'acquisizione in modalità continua:

```python
from flir_camera_wrapper import main

# Parametri di configurazione
exposure = 800  # Tempo di esposizione in microsecondi
gain = 10       # Guadagno in dB
capture_mode = 'continuous'  # Modalità di acquisizione: continuous, trigger_hw, trigger_sw

# Avvia l'acquisizione
main(exposure=exposure, gain=gain, capture_mode=capture_mode)