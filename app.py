from flask import Flask, request, render_template, jsonify

from celery.result import AsyncResult

from celery_config import celery_app

from tasks import procesar_cuestionario_task

from services import *

app = Flask(__name__)

# ============================================
# CREAR TABLAS
# ============================================

crear_tabla_si_no_existe()

# ============================================
# LOADING + CREAR TASK
# ============================================

@app.route("/cuestionario1/")
def cuestionario_loader():

    id_user = request.args.get("id_user")
    quizid = request.args.get("quizid")
    documento_usuario = request.args.get("documento_usuario")
    nombre_usuario = request.args.get("nombre_usuario")

    if not all([id_user, quizid]):

        return render_template(
            "index.html",
            message="Falta id_user o quizid en la URL"
        )

    # =====================================
    # CREAR TASK CELERY
    # =====================================

    tarea = procesar_cuestionario_task.delay(
        id_user,
        quizid,
        documento_usuario,
        nombre_usuario
    )

    # =====================================
    # MOSTRAR LOADING
    # =====================================

    return render_template(
        "loading.html",
        task_id=tarea.id,
        id_user=id_user,
        quizid=quizid,
        documento_usuario=documento_usuario,
        nombre_usuario=nombre_usuario
    )

# ============================================
# CONSULTAR ESTADO TASK
# ============================================

@app.route("/task/<task_id>")
def task_status(task_id):

    tarea = AsyncResult(
        task_id,
        app=celery_app
    )

    return jsonify({
        "ready": tarea.ready()
    })

# ============================================
# RESULTADO FINAL
# ============================================

@app.route("/resultado/<task_id>")
def resultado(task_id):

    tarea = AsyncResult(
        task_id,
        app=celery_app
    )

    if not tarea.ready():

        return "Aún procesando..."

    resultado = tarea.result

    return render_template(
        "index.html",
        intentos=resultado["intentos"],
        user=resultado["user"],
        quiz=resultado["quiz"]
    )

# ============================================
# API POSTGRES
# ============================================

@app.route("/api/postgres/detalle_intentos", methods=["POST"])
def api_detalle_intentos():

    data = request.get_json()

    id_user = data.get("id_user")
    quiz_id = data.get("quiz_id")

    if not id_user or not quiz_id:

        return jsonify({
            "error": "Faltan parámetros"
        }), 400

    conn = get_db_connection()

    cur = conn.cursor()

    cur.execute("""
        WITH ranked AS (
            SELECT 
                id_user,
                documento_usuario,
                quizid,
                intento_num,
                pregunta_num,
                enunciado,
                respuesta_estudiante,
                retroalimentacion,
                puntaje_obtenido,
                puntaje_maximo,
                fecha,
                ROW_NUMBER() OVER (
                    PARTITION BY id_user, documento_usuario, quizid, intento_num, pregunta_num
                    ORDER BY puntaje_obtenido DESC, fecha DESC
                ) AS rn
            FROM public.retroalimentaciones
            WHERE id_user = %s
              AND quizid = %s
        )
        SELECT 
            id_user,
            documento_usuario,
            quizid,
            intento_num,
            pregunta_num,    
            puntaje_obtenido,
            puntaje_maximo,
            fecha,
            NOW() AS fecha_actualizacion
        FROM ranked
        WHERE rn = 1
        ORDER BY quizid, intento_num, pregunta_num;
    """, (id_user, str(quiz_id)))

    rows = cur.fetchall()

    conn.close()

    return jsonify(rows)

# ============================================
# RUN
# ============================================

if __name__ == "__main__":

    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )