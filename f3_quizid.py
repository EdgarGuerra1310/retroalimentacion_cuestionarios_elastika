import requests
import json

# === CONFIGURACIÓN ===
token = "934a5bc65d092299e862902196a6f43b"
domain = "https://campusvirtual-sifods.minedu.gob.pe"
restformat = "json"

courseid = 2369  # tu curso

# === FUNCIÓN: obtener contenidos del curso ===
function = "mod_quiz_get_quizzes_by_courses"
params = {
    "wstoken": token,
    "wsfunction": function,
    "moodlewsrestformat": restformat,
    "courseids[0]": courseid
}

resp = requests.get(f"{domain}/webservice/rest/server.php", params=params)
quizzes = resp.json().get("quizzes", [])

for quiz in quizzes:
    print(f"📌 QuizID={quiz['id']} | Nombre={quiz['name']} | Calificación máxima={quiz['sumgrades']}")
