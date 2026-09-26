from laya import Router


schema = {
    "type": "object",
    "properties": {
        "toxizitaet": {
            "type": "string",
            "enum": ["harmlos", "unhoeflich", "beleidigend", "bedrohlich"],
            "description": (
                "Ordne die Nachricht insgesamt genau einer Stufe zu. "
                "harmlos: freundlich, neutral oder sachlich. "
                "unhoeflich: schroffer oder respektloser Ton ohne persönliche Beschimpfung "
                "und ohne ernsthafte Einschüchterung. "
                "beleidigend: direkte Beschimpfung oder persönliche Herabwürdigung, auch wenn sie "
                "sehr vulgär ist. "
                "bedrohlich: nur wenn tatsächlich Gewalt, körperlicher Schaden oder eine andere "
                "ernsthafte Konsequenz angedroht wird. Eine Beleidigung wie 'Halt die Fresse' ist "
                "nicht bedrohlich. Wenn eine Drohung vorliegt, hat bedrohlich Vorrang. "
                "Bewerte nur den Text und erfinde keinen fehlenden Kontext."
            ),
        },
        "beleidigung": {
            "type": "boolean",
            "description": (
                "true bei einer direkten Beschimpfung oder klaren persönlichen Herabwürdigung, "
                "auch bei vulgärer Sprache. Ein schroffer Befehl wie 'Sei still' allein zählt nicht."
            ),
        },
        "drohung": {
            "type": "boolean",
            "description": (
                "true, wenn der Sprecher dem Empfänger Gewalt oder körperlichen Schaden ankündigt, "
                "zum Beispiel 'Ich werde dir wehtun'. Eine bloße Beleidigung oder ein unhöflicher "
                "Befehl ist keine Drohung. Großschreibung und Ausrufezeichen allein sind keine Drohung."
            ),
        },
        "belaestigung": {
            "type": "boolean",
            "description": (
                "true nur, wenn der Text gezielte Belästigung oder Einschüchterung erkennen lässt, "
                "typischerweise durch wiederholte Angriffe oder anhaltendes Nachstellen. Eine einzelne "
                "Beleidigung zählt für sich allein nicht als Belästigung; bewerte keinen unbekannten Verlauf."
            ),
        },
    },
    "required": ["toxizitaet", "beleidigung", "drohung", "belaestigung"],
    "additionalProperties": False,
}

test_strings = [
    "Hi, alles fit??",
    "Jetzt sei doch endlich mal still!",
    "Halt die Fresse!",
    "Ich werde dir wehtun.",
    "Du bist ein wertloser Idiot.",
]

router = Router()

for test_string in test_strings:
    print(f"Test: {test_string}")
    result = router.decide(
        test_string,
        schema=schema,
        model="multilingual",
        lang_guess=["de"],
        return_details=True,
    )
    print("Vorhersage:", result.values)
    print("Konfidenz:", result.confidence)
    print("Wahrscheinlichkeiten:", result.probabilities)
