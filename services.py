import os
import re
import requests
from bs4 import BeautifulSoup
import difflib
from openai import OpenAI
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import markdown
from dotenv import load_dotenv

load_dotenv()

from openai import AzureOpenAI

MOODLE_TOKEN = os.getenv("MOODLE_TOKEN")
MOODLE_DOMAIN = os.getenv("MOODLE_DOMAIN")
RESTFORMAT = "json"

QUIZ_CONFIG = {
    "2501": {
        "preguntas_cerradas": []
    },
    "11752": {
        "preguntas_cerradas": ["1","2","3","4","5","6"]
    },

    "12036": {
        "preguntas_cerradas": ["1","2","3","4"]
    }
}

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version="2024-05-01-preview",
    azure_endpoint="https://Minedu-IA.openai.azure.com"
)

OPENAI_PRICES = {
    "gpt-4o-mini": {
        "prompt": 0.15 / 1_000_000,
        "completion": 0.60 / 1_000_000
    }
}

def obtener_retro_visible(retro_completo):
    if not retro_completo:
        return ""

    # Extraer puntaje
    puntaje_match = re.search(r"Puntaje total obtenido:\s*(.+)", retro_completo)

    # Extraer resumen final
    resumen_match = re.search(r"\*\*Retroalimentación final:\*\*\s*(.+)", retro_completo, re.DOTALL)

    puntaje = puntaje_match.group(1).strip() if puntaje_match else ""
    resumen = resumen_match.group(1).strip() if resumen_match else ""

    return f"**Puntaje:** {puntaje}\n\n{resumen}"

def calcular_costos(modelo, usage):
    if not usage:
        return 0, 0, 0, 0, 0, 0

    precios = OPENAI_PRICES[modelo]

    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    total_tokens = usage.total_tokens

    costo_prompt = prompt_tokens * precios["prompt"]
    costo_completion = completion_tokens * precios["completion"]
    costo_total = costo_prompt + costo_completion

    return (
        prompt_tokens,
        completion_tokens,
        total_tokens,
        costo_prompt,
        costo_completion,
        costo_total
    )

# === CARGAR RESPUESTAS ESPERADAS ===
RESPUESTAS_ESPERADAS = {}
with open("respuesta_esperada_1.txt", "r", encoding="utf-8") as f:
    for linea in f:
        partes = linea.strip().split("|", 2)
        if len(partes) == 3:
            quizid, numero, respuesta = partes
            quizid = quizid.strip()
            numero = numero.strip()
            respuesta = respuesta.strip()
            if quizid not in RESPUESTAS_ESPERADAS:
                RESPUESTAS_ESPERADAS[quizid] = {}
            RESPUESTAS_ESPERADAS[quizid][numero] = respuesta

# === CARGAR RÚBRICAS ===
RUBRICAS = {}
if os.path.exists("rubricas.txt"):
    df_rub = pd.read_csv("rubricas.txt", sep="|")
    for _, r in df_rub.iterrows():
        qid = str(r["quizid"]).strip()
        num = str(r["pregunta"]).strip()
        if qid not in RUBRICAS:
            RUBRICAS[qid] = {}
        if num not in RUBRICAS[qid]:
            RUBRICAS[qid][num] = []
        RUBRICAS[qid][num].append(r.to_dict())



# === CONEXIÓN A POSTGRESQL ===
def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        cursor_factory=RealDictCursor
    )
    return conn

# === CREAR TABLA SI NO EXISTE ===
def crear_tabla_si_no_existe():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS retroalimentaciones (
            id SERIAL PRIMARY KEY,
            id_user VARCHAR(50),
            documento_usuario VARCHAR(50),
            quizid VARCHAR(50),
            intento_num INTEGER,
            pregunta_num VARCHAR(10),
            enunciado TEXT,
            respuesta_estudiante TEXT,
            retroalimentacion TEXT,
            puntaje_obtenido NUMERIC,
            puntaje_maximo NUMERIC,
            fecha TIMESTAMP DEFAULT NOW()
        );
        
        CREATE TABLE IF NOT EXISTS openai_usage_retroalimentaciones (
            id SERIAL PRIMARY KEY,
            id_user VARCHAR(50),
            quizid VARCHAR(50),
            intento_num INTEGER,
            pregunta_num VARCHAR(10),

            tipo_interaccion VARCHAR(50),
            modelo VARCHAR(50),

            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,

            costo_prompt_usd NUMERIC(10,6),
            costo_completion_usd NUMERIC(10,6),
            costo_total_usd NUMERIC(10,6),

            fecha TIMESTAMP DEFAULT NOW()
        );

    """)
    conn.commit()
    conn.close()

crear_tabla_si_no_existe()

# === FUNCIONES DE BASE DE DATOS ===
def guardar_uso_openai(
    id_user, quizid, intento_num, pregunta_num,
    tipo_interaccion, modelo,
    prompt_tokens, completion_tokens, total_tokens,
    costo_prompt, costo_completion, costo_total
):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO openai_usage_retroalimentaciones (
            id_user, quizid, intento_num, pregunta_num,
            tipo_interaccion, modelo,
            prompt_tokens, completion_tokens, total_tokens,
            costo_prompt_usd, costo_completion_usd, costo_total_usd
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        id_user, quizid, intento_num, pregunta_num,
        tipo_interaccion, modelo,
        prompt_tokens, completion_tokens, total_tokens,
        costo_prompt, costo_completion, costo_total
    ))
    conn.commit()
    conn.close()
    
def obtener_retroalimentacion_guardada(id_user, quizid, intento_num):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT pregunta_num, retroalimentacion, enunciado, respuesta_estudiante,
               puntaje_obtenido, puntaje_maximo
        FROM retroalimentaciones
        WHERE id_user = %s AND quizid = %s AND intento_num = %s
    """, (id_user, quizid, intento_num))
    filas = cur.fetchall()
    conn.close()
    return {f["pregunta_num"]: f for f in filas}

def guardar_retroalimentacion(id_user, documento_usuario, quizid, intento_num, pregunta_num, enunciado, respuesta_estudiante, retroalimentacion, puntaje_obtenido=None, puntaje_maximo=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO retroalimentaciones (
            id_user, documento_usuario, quizid, intento_num, pregunta_num,
            enunciado, respuesta_estudiante, retroalimentacion,
            puntaje_obtenido, puntaje_maximo
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        id_user, documento_usuario, quizid, intento_num, pregunta_num,
        enunciado, respuesta_estudiante, retroalimentacion,
        puntaje_obtenido, puntaje_maximo
    ))
    conn.commit()
    conn.close()

# === FUNCIONES DE MOODLE ===
def moodle_get(params):
    resp = requests.get(f"{MOODLE_DOMAIN}/webservice/rest/server.php", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_user_attempts(quizid, userid):
    params = {
        "wstoken": MOODLE_TOKEN,
        "wsfunction": "mod_quiz_get_user_attempts",
        "moodlewsrestformat": RESTFORMAT,
        "quizid": quizid,
        "userid": userid,
        "status": "all"
    }
    return moodle_get(params)

def get_attempt_review(attemptid):
    params = {
        "wstoken": MOODLE_TOKEN,
        "wsfunction": "mod_quiz_get_attempt_review",
        "moodlewsrestformat": RESTFORMAT,
        "attemptid": attemptid,
        "page": -1
    }
    return moodle_get(params)

# === PARSEO DE PREGUNTA ===
def parse_question_html(html):
    soup = BeautifulSoup(html, "html.parser")
    enunciado_div = soup.find("div", class_="qtext")
    enunciado = enunciado_div.get_text(" ", strip=True) if enunciado_div else soup.get_text(" ", strip=True)[:300]
    right_ans = soup.find("div", class_="rightanswer")
    respuesta_correcta = right_ans.get_text(" ", strip=True).replace("La respuesta correcta es:", "").strip() if right_ans else ""
    texto_plano = soup.get_text(" ", strip=True)
    match = re.search(r"Guardada:\s*(.+?)\s*Respuesta", texto_plano)
    if match:
        respuesta_estudiante = match.group(1).strip()
    else:
        respuesta_estudiante = texto_plano.split("Guardada:")[-1].strip() if "Guardada:" in texto_plano else "⛔ No disponible"
    return enunciado, respuesta_estudiante, respuesta_correcta

# === SIMILITUD Y CATEGORÍA ===
def similarity_ratio(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

def evaluar_respuesta(quizid, numero, respuesta_estudiante):
    quizid_str = str(quizid).strip()
    numero_str = str(numero).strip()

    ref = RESPUESTAS_ESPERADAS.get(quizid_str, {}).get(numero_str, "")
    if not ref:
        return "⚠️ No hay respuesta esperada registrada para esta pregunta en este cuestionario."
    if not respuesta_estudiante or respuesta_estudiante == "⛔ No disponible":
        return "❌ No encontré respuesta guardada. Pide al estudiante que finalice y envíe su intento."

    sim = similarity_ratio(respuesta_estudiante, ref)
    if sim > 0.85:
        categoria = "🌟 Perfecto"
        desc = "Tu respuesta es muy similar a lo esperado."
    elif sim > 0.65:
        categoria = "✅ Bueno"
        desc = "Tu respuesta es buena, aunque podrías fortalecer algunos aspectos."
    elif sim > 0.45:
        categoria = "🟡 Regular"
        desc = "Tu respuesta tiene relación, pero necesita mayor desarrollo o claridad."
    else:
        categoria = "❌ Revisar"
        desc = "Tu respuesta difiere bastante de lo esperado."

    return f"{categoria}: {desc} (Similitud {sim:.2f})"

# === RETROALIMENTACIÓN IA ===
def generar_retroalimentacion_ia(enunciado, respuesta_estudiante, respuesta_esperada,
                                 id_user, quizid, intento_num, pregunta_num, nombre_usuario):
    prompt = f"""
    Actúa como un formador pedagógico experto en retroalimentación formativa.
    El estudiante se llama {nombre_usuario}.
    Dirígete a él por su nombre en la retroalimentación.

    La pregunta es:
    "{enunciado}"

    La respuesta del estudiante fue:
    "{respuesta_estudiante}"

    La respuesta esperada de referencia es:
    "{respuesta_esperada}"

    Da una retroalimentación formativa que:
    - NO revele la respuesta correcta.
    - Destaque algún aspecto positivo si lo hay.
    - Oriente al estudiante en base a la respuesta esperada brindando algunos tips que solo orienten.
    - Use tono empático y constructivo, en no más de 3 líneas.
    - En el caso de que no tenga nada que ver con lo esperado, menciona amablemente que no cumple con lo esperado.
    """
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1000
        )

        usage = completion.usage

        (
            prompt_tokens,
            completion_tokens,
            total_tokens,
            costo_prompt,
            costo_completion,
            costo_total
        ) = calcular_costos("gpt-4o-mini", usage)

        guardar_uso_openai(
            id_user=id_user,
            quizid=quizid,
            intento_num=intento_num,
            pregunta_num=pregunta_num,
            tipo_interaccion="feedback_simple",
            modelo="gpt-4o-mini",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            costo_prompt=costo_prompt,
            costo_completion=costo_completion,
            costo_total=costo_total
        )

        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Error al generar retroalimentación con IA: {e}"
def limpiar_numero(valor):
    if valor is None:
        return 0.0

    if isinstance(valor, str):
        valor = valor.replace(",", ".")  # 👈 clave
        try:
            return float(valor)
        except:
            return 0.0

    return float(valor)

def es_pregunta_cerrada(quizid, numero):
    quizid = str(quizid)
    numero = str(numero)

    config = QUIZ_CONFIG.get(quizid, {})
    cerradas = config.get("preguntas_cerradas", [])

    return numero in cerradas

# === RETROALIMENTACIÓN CON RÚBRICA ===
def generar_retroalimentacion_con_rubrica_ia(quizid, numero, respuesta_estudiante, respuesta_esperada,id_user, intento_num, nombre_usuario):
    quizid_str, numero_str = str(quizid).strip(), str(numero).strip()
    print(quizid_str)
    print(numero_str)
    print(RUBRICAS)
    if quizid_str not in RUBRICAS or numero_str not in RUBRICAS[quizid_str]:
        return None

    criterios = RUBRICAS[quizid_str][numero_str]

    # Calcular puntaje máximo real (suma de los puntaje_destacado)
    puntaje_maximo_total = sum(float(c["puntaje_destacado"]) for c in criterios if "puntaje_destacado" in c)

    # === Construir prompt base ===
    prompt = [
        "Eres un docente evaluador experto en formación de formadores del Ministerio de Educación.",
        f"El estudiante se llama {nombre_usuario}.",
        "Evalúa la respuesta del estudiante según la siguiente rúbrica.",
        "Por cada criterio, indica el **Nivel alcanzado** (Insuficiente, En proceso, Satisfactorio o Destacado) y una **justificación breve**.",
        "Solo devuelve texto estructurado claro y coherente.",
        "La respuesta debe ser en segunda persona, dirigiendose directamente",
        "En el caso de que no tenga nada que ver con lo esperado, menciona amablemente que no cumple con lo esperado.",
        "identifica lo escrito en la respuesta del estudiante, que sea coherente y tenga sentido",
        "la respuesta del estudiante tiene que tener un sentido lógico de la pregunta que se tiene, una explicación clara, no es suficiente con solo dar palabras sin ideas",
        "",
        f"Respuesta del estudiante: {respuesta_estudiante}",
        f"Respuesta esperada: {respuesta_esperada}",
        "De ser necesario brinda un ejemplo corto de la respuesta esperada",
        "",
        "Rúbrica:"
    ]

    prompt.append("""
        IMPORTANTE:
        Devuelve EXACTAMENTE este formato:

        Criterio: [nombre del criterio]
        Nivel alcanzado: Insuficiente|En proceso|Satisfactorio|Destacado
        Justificación: ...

        No uses encabezados Markdown (####).
        No uses viñetas.
        No uses negritas.
        """)

    #print(respuesta_estudiante)
    for c in criterios:
        prompt.append(
            f"- Criterio: {c['criterio']}\n"
            f"  - Insuficiente: {c['insuficiente']}\n"
            f"  - En proceso: {c['en_proceso']}\n"
            f"  - Satisfactorio: {c['satisfactorio']}\n"
            f"  - Destacado: {c['destacado']}"
        )

    prompt_text = "\n".join(prompt)


    

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.4,
            max_tokens=1500
        )
        respuesta_ia = completion.choices[0].message.content.strip()

        # === GUARDAR USO OPENAI (RÚBRICA DETALLE) ===
        usage = completion.usage

        (
            prompt_tokens,
            completion_tokens,
            total_tokens,
            costo_prompt,
            costo_completion,
            costo_total
        ) = calcular_costos("gpt-4o-mini", usage)

        guardar_uso_openai(
            id_user=id_user,                 # aquí aún no lo tienes
            quizid=quizid,
            intento_num=intento_num,
            pregunta_num=numero_str,
            tipo_interaccion="rubrica_detalle",
            modelo="gpt-4o-mini",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            costo_prompt=costo_prompt,
            costo_completion=costo_completion,
            costo_total=costo_total
        )


        puntaje_total = 0.0
        for c in criterios:
            criterio = c["criterio"]
            bloque = re.search(rf"(Criterio:\s*{re.escape(criterio)}.*?)(?=(?:\nCriterio:|$))", respuesta_ia, re.IGNORECASE | re.DOTALL)
            print(c)
            print(bloque)
            if not bloque:
                continue
            #print(bloque)
            texto_bloque = bloque.group(1)
            #print(texto_bloque)
            #nivel_match = re.search(r"Nivel alcanzado:\s*(Insuficiente|En proceso|Satisfactorio|Destacado)", texto_bloque, re.IGNORECASE)
            nivel_match = re.search(r"\*{0,2}\s*Nivel\s+alcanzado\s*\*{0,2}\s*:\s*\*{0,2}\s*(Insuficiente|En\s*proceso|Satisfactorio|Destacado)\*{0,2}",texto_bloque,re.IGNORECASE)
            if not nivel_match:
                continue
            nivel = nivel_match.group(1).strip().lower()
            #print(nivel)
            if "insuficiente" in nivel:
                puntaje = float(c["puntaje_insuficiente"])
            elif "proceso" in nivel:
                puntaje = float(c["puntaje_en_proceso"])
            elif "satisfactorio" in nivel:
                puntaje = float(c["puntaje_satisfactorio"])
            elif "destacado" in nivel:
                puntaje = float(c["puntaje_destacado"])
            else:
                puntaje = 0.0
            puntaje_total += puntaje

            nuevo_bloque = re.sub(
                r"(Nivel alcanzado:\s*(?:Insuficiente|En proceso|Satisfactorio|Destacado))",
                rf"\1 ({puntaje}/{c['puntaje_destacado']})",
                texto_bloque,
                count=1,
                flags=re.IGNORECASE
            )
            respuesta_ia = respuesta_ia.replace(texto_bloque, nuevo_bloque)

        # === Retroalimentación final ===
        prompt_resumen = f"""
Eres un evaluador educativo. Redacta una retroalimentación final general basada en los criterios evaluados y sus justificaciones.

{respuesta_ia}

El puntaje total obtenido es {puntaje_total:.2f} sobre un máximo de {puntaje_maximo_total:.2f}.
El estudiante se llama {nombre_usuario}, redacta de 4 a 5 líneas describiendo fortalezas y aspectos a mejorar sin mencionar el nivel alcanzado ni el desempeño obtenido. Dirigete al
docente con su nombre en primera persona como si fueras un formador.
"""
        print('--------------------------------nombre del usuario--------------------')
        print(nombre_usuario)
        completion_final = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt_resumen}],
            temperature=0.5,
            max_tokens=300
        )
        resumen = completion_final.choices[0].message.content.strip()

        # === GUARDAR USO OPENAI (RÚBRICA RESUMEN) ===
        usage = completion_final.usage

        (
            prompt_tokens,
            completion_tokens,
            total_tokens,
            costo_prompt,
            costo_completion,
            costo_total
        ) = calcular_costos("gpt-4o-mini", usage)

        guardar_uso_openai(
            id_user=id_user,
            quizid=quizid,
            intento_num=intento_num,
            pregunta_num=numero_str,
            tipo_interaccion="rubrica_resumen",
            modelo="gpt-4o-mini",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            costo_prompt=costo_prompt,
            costo_completion=costo_completion,
            costo_total=costo_total
        )

        retro_completo = f"""
        **Retroalimentación por rúbrica (Pregunta {numero_str})**

        {respuesta_ia}

        ---

        **Puntaje total obtenido:** {puntaje_total:.2f} / {puntaje_maximo_total:.2f}

        **Retroalimentación final:** {resumen}
        """

        # 👇 SOLO LO QUE VE EL USUARIO
        retro_visible = f"""
        **Puntaje:** {puntaje_total:.2f} / {puntaje_maximo_total:.2f}

        {resumen}
        """

        return retro_completo.strip(), retro_visible.strip(), puntaje_total, puntaje_maximo_total

    except Exception as e:
        return (
            f"⚠️ Error al generar retroalimentación con rúbrica: {e}",
            "",
            0,
            0
        )

def existe_registro(id_user, quizid, intento_num, pregunta_num):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM retroalimentaciones
        WHERE id_user=%s AND quizid=%s AND intento_num=%s AND pregunta_num=%s
        LIMIT 1
    """, (id_user, quizid, intento_num, pregunta_num))
    
    existe = cur.fetchone()
    conn.close()
    return existe is not None

def calcular_resumen_intento(preguntas):

    dimensiones = {
        "I. Evaluación de conocimientos": {
            "preguntas": list(range(1, 7)),
            "total": 0
        },

        "II. Evaluación de análisis y reflexión crítica del formador": {
            "preguntas": list(range(7, 10)),
            "total": 0
        },

        "III. Evaluación del nivel actitudinal e integridad del formador": {
            "preguntas": list(range(10, 12)),
            "total": 0
        }
    }

    total_general = 0

    for q in preguntas:

        num = int(q["pregunta"])

        puntaje = limpiar_numero(
            q.get("puntaje_obtenido")
        )

        for dim, data in dimensiones.items():

            if num in data["preguntas"]:

                data["total"] += puntaje

                break

        total_general += puntaje

    return dimensiones, total_general