# Flir Capture

Questo repository fornisce un wrapper Python per l'acquisizione di immagini da telecamere FLIR utilizzando il framework PySpin. Il progetto è progettato per semplificare l'integrazione delle telecamere FLIR in applicazioni di visione artificiale, offrendo un'interfaccia di alto livello e gestendo la complessità della configurazione e dell'acquisizione multithread.

Il wrapper supporta **quattro** modalità di acquisizione:
-   **`continuous`**: Acquisizione continua dei frame.
-   **`trigger_hw`**: Acquisizione di un singolo frame per ogni impulso del trigger hardware (es. fronte di salita/discesa).
-   **`trigger_sw`**: Acquisizione di un singolo frame su comando software.
-   **`trigger_gated`**: Acquisizione continua di frame solo per la durata in cui il segnale del trigger hardware rimane a livello logico alto. Questa modalità è ideale per scenari come l'ispezione di oggetti che passano su un nastro trasportatore.

Dagli script ufficiali si nota un comportamento non corretto con l'utilizzo del trigger software:
```python
# TODO: Blackfly and Flea3 GEV cameras need 2 second delay after software trigger
```

---

## Caratteristiche principali

-   **Gestione Avanzata delle Modalità di Acquisizione**:
    -   Modalità continua (`continuous`)
    -   Modalità trigger hardware a impulso (`trigger_hw`)
    -   Modalità trigger software (`trigger_sw`)
    -   **Modalità trigger "gated" / "level-controlled" (`trigger_gated`)**: Acquisisce frame continuamente finché il segnale di trigger hardware è alto.

-   **Architettura Produttore-Consumatore**: Ogni telecamera opera in un thread dedicato (il "produttore") che si occupa dell'acquisizione delle immagini. Questo disaccoppia l'hardware dal thread principale dell'applicazione (il "consumatore"), garantendo massima reattività e prevenendo la perdita di frame anche sotto carico.

-   **Configurazione Personalizzabile**: Permette di impostare facilmente parametri chiave come esposizione, guadagno e modalità di acquisizione all'avvio.

-   **Visualizzazione in Tempo Reale**: Utilizza OpenCV per visualizzare i flussi video dalle telecamere con un overhead minimo.

---

## Requisiti

-   Python 3.7 o superiore
-   Librerie necessarie:
    -   `PySpin`
    -   `opencv-python`
    -   `numpy`
    -   `path`
-   Un sistema compatibile con le telecamere FLIR e il framework Spinnaker SDK installato.

---

## Installazione

1.  Clonare il repository:
    ```bash
    git clone https://github.com/berserkrambo/flir_capture.git
    cd flir_capture
    ```
2.  Installare le dipendenze Python:
    ```bash
    pip install -r requirements.txt
    ```
3.  Scaricare e installare l'SDK Spinnaker corretto per il proprio sistema operativo e la propria versione di Python. Ad esempio, per Ubuntu 22.04 e Python 3.10:
    -   **SDK di sistema**: `spinnaker-4.2.0.46-amd64-22.04-pkg.tar.gz`
    -   **Wrapper Python**: `spinnaker_python-4.2.0.46-cp310-cp310-linux_x86_64-22.04`

---

## Esempio di utilizzo

### Configurazione di base

È possibile avviare lo script principale specificando i parametri di acquisizione desiderati.

```python
# main.py

import time

# Parametri di configurazione
exposure = 8000  # Tempo di esposizione in microsecondi (es. 8ms)
gain = 10       # Guadagno in dB

# Seleziona la modalità di acquisizione tra le opzioni disponibili
capture_mode = 'trigger_gated'  # Opzioni: 'continuous', 'trigger_hw', 'trigger_sw', 'trigger_gated'

# Avvia l'acquisizione
if __name__ == '__main__':
    main(exposure=exposure, gain=gain, capture_mode=capture_mode)

```

Per eseguire lo script, basta lanciare il file principale dalla riga di comando. Le finestre di OpenCV mostreranno i feed delle telecamere collegate. Premere 'q' o 'Esc' per terminare il programma.