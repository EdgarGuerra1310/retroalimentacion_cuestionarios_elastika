import requests

token = "934a5bc65d092299e862902196a6f43b"
domain = "https://campusvirtual-sifods.minedu.gob.pe"
restformat = "json"

quizid = 11877  # TU QUIZID REAL

params = {
    "wstoken": token,
    "wsfunction": "mod_quiz_get_quiz_by_id",
    "moodlewsrestformat": restformat,
    "quizids[0]": quizid
}

resp = requests.get(f"{domain}/webservice/rest/server.php", params=params)
data = resp.json()
print(data)
quiz = data.get("quizzes", [])[0]

print("📌 QuizID:", quiz.get("id"))
print("📘 CourseID:", quiz.get("course"))
print("📝 Nombre:", quiz.get("name"))