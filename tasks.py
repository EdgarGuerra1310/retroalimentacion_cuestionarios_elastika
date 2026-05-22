from celery_config import celery_app

from services import (
    get_user_attempts,
    get_attempt_review,
    parse_question_html,
    es_pregunta_cerrada,
    limpiar_numero,
    existe_registro,
    guardar_retroalimentacion,
    obtener_retroalimentacion_guardada,
    RESPUESTAS_ESPERADAS,
    generar_retroalimentacion_con_rubrica_ia,
    generar_retroalimentacion_ia,
    markdown,
    calcular_resumen_intento,
    obtener_retro_visible
)

@celery_app.task
def procesar_cuestionario_task(
    id_user,
    quizid,
    documento_usuario,
    nombre_usuario
):

    attempts_json = get_user_attempts(
        quizid=int(quizid),
        userid=int(id_user)
    )

    intentos = []

    for intento in attempts_json.get("attempts", []):

        attemptid = intento["id"]
        numero_intento = intento["attempt"]

        retro_guardadas = obtener_retroalimentacion_guardada(
            id_user,
            quizid,
            numero_intento
        )

        review = get_attempt_review(attemptid)

        preguntas = []

        for q in review.get("questions", []):

            numero = str(q.get("number", "N/A"))

            enunciado, respuesta_estudiante, _ = parse_question_html(
                q.get("html", "")
            )

            # ======================================================
            # PREGUNTAS CERRADAS
            # ======================================================

            if es_pregunta_cerrada(quizid, numero):

                puntaje_obtenido = limpiar_numero(
                    q.get("mark", 0)
                )

                puntaje_maximo = limpiar_numero(
                    q.get("maxmark", 0)
                )

                if not existe_registro(
                    id_user,
                    quizid,
                    numero_intento,
                    numero
                ):

                    guardar_retroalimentacion(
                        id_user,
                        documento_usuario,
                        quizid,
                        numero_intento,
                        numero,
                        enunciado,
                        respuesta_estudiante,
                        "",
                        puntaje_obtenido,
                        puntaje_maximo
                    )

                preguntas.append({
                    "pregunta": numero,
                    "enunciado": enunciado,
                    "respuesta_estudiante": respuesta_estudiante,
                    "es_cerrada": True,

                    # ✅ IMPORTANTE
                    "puntaje_obtenido": limpiar_numero(
                        puntaje_obtenido
                    ),

                    "puntaje_maximo": limpiar_numero(
                        puntaje_maximo
                    ),

                    "retroalimentacion": None
                })

                continue

            # ======================================================
            # SI YA EXISTE EN BD
            # ======================================================

            if numero in retro_guardadas:

                datos = retro_guardadas[numero]

                retro_completo = datos["retroalimentacion"]

                retro_final = obtener_retro_visible(
                    retro_completo
                )

                preguntas.append({
                    "pregunta": numero,

                    "enunciado": datos["enunciado"],

                    "respuesta_estudiante": datos[
                        "respuesta_estudiante"
                    ],

                    "es_cerrada": False,

                    # ✅ markdown correcto
                    "retroalimentacion": markdown.markdown(
                        retro_final if retro_final else ""
                    ),

                    # ✅ IMPORTANTÍSIMO
                    "puntaje_obtenido": limpiar_numero(
                        datos.get("puntaje_obtenido")
                    ),

                    "puntaje_maximo": limpiar_numero(
                        datos.get("puntaje_maximo")
                    )
                })

            # ======================================================
            # GENERAR NUEVA RETRO
            # ======================================================

            else:

                respuesta_esperada = RESPUESTAS_ESPERADAS.get(
                    str(quizid),
                    {}
                ).get(str(numero), "")

                retro_rubrica = generar_retroalimentacion_con_rubrica_ia(
                    quizid,
                    numero,
                    respuesta_estudiante,
                    respuesta_esperada,
                    id_user,
                    numero_intento,
                    nombre_usuario
                )

                # ==================================================
                # RETRO CON RÚBRICA
                # ==================================================

                if retro_rubrica and isinstance(retro_rubrica, tuple):

                    (
                        retro_completo,
                        retro_visible,
                        puntaje_obtenido,
                        puntaje_maximo
                    ) = retro_rubrica

                    retro_final = retro_visible

                # ==================================================
                # RETRO SIMPLE
                # ==================================================

                elif retro_rubrica:

                    retro_final = retro_rubrica

                    puntaje_obtenido = 0
                    puntaje_maximo = 0

                    retro_completo = retro_final

                # ==================================================
                # IA NORMAL
                # ==================================================

                else:

                    retro_ia = generar_retroalimentacion_ia(
                        enunciado,
                        respuesta_estudiante,
                        respuesta_esperada,
                        id_user,
                        quizid,
                        numero_intento,
                        numero,
                        nombre_usuario
                    )

                    retro_final = f"💬 {retro_ia}"

                    puntaje_obtenido = 0
                    puntaje_maximo = 0

                    retro_completo = retro_final

                # ==================================================
                # GUARDAR EN BD
                # ==================================================

                guardar_retroalimentacion(
                    id_user,
                    documento_usuario,
                    quizid,
                    numero_intento,
                    numero,
                    enunciado,
                    respuesta_estudiante,
                    retro_completo,

                    # ✅ IMPORTANTÍSIMO
                    limpiar_numero(puntaje_obtenido),

                    limpiar_numero(puntaje_maximo)
                )

                preguntas.append({

                    "pregunta": numero,

                    "enunciado": enunciado,

                    "respuesta_estudiante": respuesta_estudiante,

                    "es_cerrada": False,

                    # ✅ markdown limpio
                    "retroalimentacion": markdown.markdown(
                        retro_final if retro_final else ""
                    ),

                    # ✅ NECESARIO PARA EL RESUMEN
                    "puntaje_obtenido": limpiar_numero(
                        puntaje_obtenido
                    ),

                    "puntaje_maximo": limpiar_numero(
                        puntaje_maximo
                    )
                })

        # ==========================================================
        # CALCULAR RESUMEN
        # ==========================================================

        dimensiones, total_general = calcular_resumen_intento(
            preguntas
        )

        intentos.append({
            "numero": numero_intento,
            "preguntas": preguntas,
            "dimensiones": dimensiones,
            "total_general": total_general
        })

    return {
        "intentos": intentos,
        "user": id_user,
        "quiz": quizid
    }