import os

from typesafe_sdk import TypeSafeClient


state = """Bewerte die Toxizität der Nachricht auf einer ganzzahligen Skala von 0 bis 9.
0 bedeutet freundlich, neutral oder sachlich. 1-2 bedeutet leicht unhöflich
oder schroff. 3-5 bedeutet klar unfreundlich oder respektlos, aber ohne
aggressives Anschreien oder grobe vulgäre Beschimpfung. 6-7 bedeutet stark
aggressives Anschreien, einen aggressiven Befehl oder eine grobe vulgäre
Beschimpfung; Beispiele sind 'JETZT SEI DOCH ENDLICH STILL!' und 'HALT DIE FRESSE!'.
Großschreibung und Ausrufezeichen können den Score erhöhen, wenn sie
aggressives Anschreien vermitteln. 8 bedeutet besonders schwere, gezielte
Einschüchterung oder wiederholte schwere Angriffe, sofern dies aus dem Text
hervorgeht. 9 bedeutet eine konkrete, ernsthafte Drohung mit Gewalt oder
körperlichem Schaden. Beleidigungen und Anschreien allein sind keine Drohung.
Bewerte nur den Text und erfinde keinen fehlenden Kontext."""

questions = {
    "score": {
        "type": "choice",
        "instructions": "Gib genau einen Toxizitäts-Score von 0 bis 9 zurück.",
        "criteria": {str(score): f"Score {score}" for score in range(10)},
    },
}

client = TypeSafeClient(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api",
)

def get_prediction(input_string: str):
    result = client.system_one(
            model="jev-1.13",
            state=f"{state}\n\nNachricht: {input_string}",
            questions=questions,
        )
    return {"input": input_string, "answer": result.answers}