from queue import Queue
from threading import Lock, Thread
from time import perf_counter
import os
import shutil
import sys

import numpy as np
import sounddevice as sd
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from dotenv import load_dotenv
from websockets.sync.client import connect as ws_connect

load_dotenv()

SAMPLE_RATE = 16_000
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
TRANSCRIPT_WS_URL = os.getenv("TRANSCRIPT_WS_URL", "")


def _fit_to_terminal(prefix, text):
	# "\r" springt nur an den Anfang der letzten sichtbaren Zeile; bei
	# Zeilenumbruch blieben ältere Reste stehen und sähen wie Duplikate aus.
	width = shutil.get_terminal_size(fallback=(100, 24)).columns
	max_len = max(10, width - 1)
	full = f"{prefix}{text}"
	if len(full) <= max_len:
		return full
	# Das Ende des Transkripts zeigen, nicht den Anfang: sonst bliebe die
	# gekürzte Zeile bei wachsendem Text unverändert und wirkte eingefroren.
	available = max_len - len(prefix) - 1
	if available <= 0:
		return full[-max_len:]
	return f"{prefix}…{text[-available:]}"


def main():
	if not DEEPGRAM_API_KEY:
		print("Trage deinen Deepgram-API-Key in der Datei .env als DEEPGRAM_API_KEY ein.")
		return

	client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
	audio_queue = Queue()
	transcript_queue = Queue()
	print_lock = Lock()
	recording_started_at = [0.0]
	last_live_text = [None]
	last_final_event = [None]

	def on_message(message):
		if getattr(message, "type", None) != "TurnInfo":
			return

		text = (getattr(message, "transcript", None) or "").strip()
		event = getattr(message, "event", None)
		if not text:
			if event == "EndOfTurn" and last_live_text[0] is not None:
				with print_lock:
					if sys.stdout.isatty():
						print("\r\x1b[K", end="", flush=True)
					last_live_text[0] = None
			return

		turn_index = getattr(message, "turn_index", "?")
		window_end = getattr(message, "audio_window_end", None)
		if event == "EndOfTurn" and window_end is not None:
			turn_key = (turn_index, text, window_end)
			with print_lock:
				if turn_key == last_final_event[0]:
					return
				last_final_event[0] = turn_key

		if event != "EndOfTurn":
			# Flux liefert während des Sprechens regelmäßige Transcript-Updates.
			# Die vorläufige Zeile wird aktualisiert statt immer wieder angehängt.
			live_text = _fit_to_terminal(f"Turn {turn_index}: ", text)
			with print_lock:
				if live_text == last_live_text[0]:
					return
				last_live_text[0] = live_text
				if sys.stdout.isatty():
					print(f"\r\x1b[K{live_text}", end="", flush=True)
				# Ohne echtes TTY (z. B. Debug-Konsole) kann die Zeile nicht
				# überschrieben werden, also werden Zwischenstände dort nicht
				# ausgegeben, um Zeilenspam zu vermeiden.
			return

		latency_text = ""
		if window_end is not None:
			latency = perf_counter() - (
				recording_started_at[0] + float(window_end)
			)
			latency_text = f"[Latenz ca. {latency:.2f} s] "

		languages = getattr(message, "languages", None) or []
		language_text = f" [{', '.join(languages)}]" if languages else ""
		final_text = f"{latency_text}Turn {turn_index}{language_text}: {text}"
		with print_lock:
			if sys.stdout.isatty():
				print(f"\r\x1b[K{final_text}\n", end="", flush=True)
			else:
				print(final_text, flush=True)
			last_live_text[0] = None

		if TRANSCRIPT_WS_URL:
			# Jeder fertige Turn wird als ein Token/eine Nachricht gestreamt.
			transcript_queue.put(text)

	def on_error(error):
		with print_lock:
			print(f"Deepgram-Fehler: {error}", flush=True)

	with client.listen.v2.connect(
		model="flux-general-multi",
		language_hint="de",
		encoding="linear16",
		sample_rate=SAMPLE_RATE,
	) as connection:
		connection.on(EventType.MESSAGE, on_message)
		connection.on(EventType.ERROR, on_error)

		listener = Thread(target=connection.start_listening, daemon=True)
		listener.start()

		def send_audio():
			while True:
				packet = audio_queue.get()
				try:
					if packet is None:
						return
					connection.send_media(packet)
				except Exception as error:
					with print_lock:
						print(f"Audio konnte nicht gesendet werden: {error}", flush=True)
				finally:
					audio_queue.task_done()

		sender = Thread(target=send_audio, daemon=True)
		sender.start()

		def send_transcripts():
			ws = None
			try:
				while True:
					token = transcript_queue.get()
					try:
						if token is None:
							return
						if ws is None:
							ws = ws_connect(TRANSCRIPT_WS_URL, open_timeout=5)
						ws.send(token)
					except Exception as error:
						with print_lock:
							print(f"Transkript konnte nicht gestreamt werden: {error}", flush=True)
						ws = None
					finally:
						transcript_queue.task_done()
			finally:
				if ws is not None:
					ws.close()

		transcript_sender = Thread(target=send_transcripts, daemon=True)
		if TRANSCRIPT_WS_URL:
			transcript_sender.start()

		def capture_audio(indata, frames, time_info, status):
			if status:
				with print_lock:
					print(f"Mikrofon-Hinweis: {status}", flush=True)

			# sounddevice liefert Float-Audio; Deepgram erhält 16-Bit-PCM.
			pcm = (np.clip(indata[:, 0], -1.0, 1.0) * 32767).astype(np.int16)
			audio_queue.put(pcm.tobytes())

		input("Drücke Enter, um die Live-Aufnahme zu starten ...")
		print("Aufnahme läuft. Drücke Enter, um sie zu beenden.", flush=True)
		recording_started_at[0] = perf_counter()
		try:
			with sd.InputStream(
				samplerate=SAMPLE_RATE,
				channels=1,
				dtype="float32",
				blocksize=0,  # kein festes Chunking: PortAudio liefert Audio mit minimaler Latenz
				latency="low",
				callback=capture_audio,
			):
				input()
		finally:
			audio_queue.put(None)
			sender.join()
			if TRANSCRIPT_WS_URL:
				transcript_queue.put(None)
				transcript_sender.join(timeout=5)
			connection.send_close_stream()
			listener.join(timeout=5)

	print("Aufnahme beendet.")


if __name__ == "__main__":
	main()

