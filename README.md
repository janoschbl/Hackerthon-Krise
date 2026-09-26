# Hackerthon-Krise

## Sprache zu Text

Das Programm streamt Sprache vom Standardmikrofon mit Deepgrams
`flux-general-multi` live in die Cloud. Es bevorzugt Deutsch und gibt
Transkriptionen pro Gesprächsbeitrag (Turn) mit erkannter Sprache aus. Die
Erkennung benötigt Internet; Audio wird zur Verarbeitung an Deepgram übertragen.

Flux gibt in diesem Modell keine Sprecher-IDs für verschiedene Personen aus.
Die angezeigte Turn-Nummer bezeichnet einen Gesprächsbeitrag, nicht eine Person.

1. Erstelle einen API-Key in deinem Deepgram-Konto und trage ihn oben in
   `Speech2Text.py` bei `DEEPGRAM_API_KEY` ein.
2. Installiere die Python-Abhängigkeiten: `pip install -r requirements.txt`
3. Starte das Programm: `python Speech2Text.py`
4. Drücke Enter zum Starten der Aufnahme und noch einmal Enter zum Beenden.

Hinweis: Ein fest im Quellcode gespeicherter Schlüssel kann offengelegt werden,
wenn du die Datei teilst oder in ein öffentliches Git-Repository hochlädst.

Die angezeigte Latenz ist eine ungefähre Messung vom Ende des erkannten
Audioabschnitts bis zur Antwort von Deepgram.