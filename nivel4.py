import numpy as np
import json
import os
import httpx # Importado para hacer llamadas HTTP a los jurados
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from typing import Union 
import redis.asyncio as redis
from google.cloud import tasks_v2 # Importado para Cloud Tasks
from google.protobuf import timestamp_pb2
import logging

# --- Configuración de Logging ---
# Google Cloud Run captura automáticamente los 'print()' y 'logging' a stdout/stderr
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONSTANTES DE CONFIGURACIÓN (¡RELLENAR ESTAS!) ---
PROJECT_ID = os.getenv("PROJECT_ID", "carrera-pods-gcossa")
QUEUE_ID = os.getenv("QUEUE_ID", "jurados-pods") 
LOCATION_ID = os.getenv("LOCATION_ID", "us-central1")

# ¡IMPORTANTE! Esta es la URL de tu propio servicio de Cloud Run
# Cloud Tasks la necesita para saber a quién llamar (al worker)
# Reemplaza esto con tu URL de Cloud Run
SERVICE_URL = os.getenv("SERVICE_URL", "https://carrera-pods-api-79403090209.us-central1.run.app")

# Lista de los 3 servicios de jurados (URLs de ejemplo)
JUROR_URLS = [
    "https://jurado-1.intergalactico.com/api/v1/notificar",
    "https://jurado-2.otro-planeta.com/v1/resultado",
    "https://jurado-3.caotico.com/endpoint/recibir"
]

# Cliente de Cloud Tasks
tasks_client = None
task_queue_path = None
try:
    tasks_client = tasks_v2.CloudTasksClient()
    task_queue_path = tasks_client.queue_path(PROJECT_ID, LOCATION_ID, QUEUE_ID)
except Exception as e:
    logger.error(f"No se pudo inicializar el cliente de Cloud Tasks: {e}")


# Coordenadas de posicionamiento de las antenas
antena0 = [-500, -200]
antena1 = [100, -100]
antena2 = [500, 100]

try:
    redisCliente = redis.Redis(host = os.getenv("REDIS_HOST", "localhost"), 
                port = 6379, 
                db = 0, 
                decode_responses = True)
except:
    print("No se pudo conectar con el servidor de Redis")
    redisCliente = None

def ObtenerPosicionPod(distancias):
    for d in distancias:
        if d < 0:
            raise HTTPException(status_code=400, detail="Las distancias no pueden ser negativas")
    posAntena0, posAntena1, posAntena2 = np.array(antena0), np.array(antena1), np.array(antena2) # Las posiciones de las antenas se convierten en arrays de numpy para facilitar los calculos
    # Vectores entre antenas
    ejeX = (posAntena1 - posAntena0) / np.linalg.norm(posAntena1 - posAntena0)
    i = np.dot(ejeX, posAntena2 - posAntena0)
    ejeY = (posAntena2 - posAntena0 - i * ejeX) / np.linalg.norm(posAntena2 - posAntena0 - i * ejeX)
    d = np.linalg.norm(posAntena1 - posAntena0)
    j = np.dot(ejeY, posAntena2 - posAntena0)
    # Cálculo de coordenadas x e y
    x = (distancias[0]**2 - distancias[1]**2 + d**2) / (2 * d)
    y = ((distancias[0]**2 - distancias[2]**2 + i**2 + j**2) / (2 * j)) - (i / j) * x
    # Posición final
    posNave = posAntena0 + x * ejeX + y * ejeY
    return round(float(posNave[0]),2), round(float(posNave[1]), 2)


def ObtenerMetricasPod(mensajes): # @mensajes es una lista de listas
    '''
    Consideraciones:
        - La unidad de la metrica tiene que ser congruente con la unidad de la metrica recibida, por lo tanto si 2 o mas mensajes tienen la misma mètrica con diferente unidad, se toma el valor cuya unidad es congruente con la metrica.
        - Para aceptar una métrica, 2 o mas antenas deben coincidir en el valor y unidad de la métrica.
        - Si lo anterior no se cumple, no se toma ninguna métrica hasta realizar otro sensado.
    '''
    unidadesMetricasValidas = ('C', 'Wh', 'C', '%')
    cantidadMetricas = len(mensajes[0])
    matrizMetricas = np.array(mensajes)
    metricasObtenida = []
    for i in range(cantidadMetricas):
        vectorMetricas = list(filter(lambda metrica: metrica != "" and metrica.endswith(unidadesMetricasValidas[i]), list(matrizMetricas[:, i]))) # [590C, 60%, 110C] >> [590C, 110C]
        valoresMetricas = [metrica.replace(unidadesMetricasValidas[i], "") for metrica in vectorMetricas] #[590C, 110C] >> [590, 110]
        for valor in valoresMetricas:
            if valoresMetricas.count(valor) >= 2:  # Si 2 o mas antenas coinciden en el valor de la métrica
                metricasObtenida.append(valor + unidadesMetricasValidas[i])
                break
            else:
                raise HTTPException(status_code=404, detail="No se pudieron determinar todas las métricas del POD debido a inconsistencias en los datos recibidos de las antenas.")
    return metricasObtenida


app = FastAPI()
# Creamos un cliente HTTP global para reutilizar conexiones
http_client = httpx.AsyncClient()

class DatosAntena(BaseModel):
    name: str
    pod: str
    distance: float
    metrics: list[str]

class InfoAntenas(BaseModel):
    antenas: list[DatosAntena]

class DataPod(BaseModel):
    pod: str
    distance: float
    message: list[str]

# --- ¡NUEVO MODELO PARA EL WORKER DE CLOUD TASKS! ---
class JurorTask(BaseModel):
    juror_url: str # La URL del jurado externo al que llamar
    payload: dict  # El JSON (resultado) que debemos enviarles


# Creamos un cliente HTTP global para reutilizar conexiones
http_client = httpx.AsyncClient()

@app.post("/podhealth/")
async def InfoPod(data: InfoAntenas):
    posicionPodx, posicionPody = ObtenerPosicionPod([antena.distance for antena in data.antenas])
    dataPod = {"pod": data.antenas[0].pod,
                "position": {"x": posicionPodx, 
                             "y": posicionPody},
                "metrics": ObtenerMetricasPod([antena.metrics for antena in data.antenas])}
    return dataPod


@app.post("/podhealth_split/{antena_name}")
async def GuardarInfoAntenaPod(antena_name:str, data: DataPod):
    if not redisCliente:
        raise HTTPException(status_code=500, detail="No se pudo conectar con el servidor de Redis")
    await redisCliente.hset(data.pod, antena_name, json.dumps({"distance": data.distance,"message": data.message})) #Ej: {"Anakin Skywalker": {"antena0":{"distance": 250.5, "message": ["590C", "60%", "110C"]}}}
    return  {"message" : f"Datos de antena {antena_name} almacenados en Redis para Pod '{data.pod}'",
             "data": await redisCliente.hgetall(data.pod)}


@app.get("/podhealth_split/{nombrePod}")
async def ObtenerInfoPod(nombrePod: str):
    if not await redisCliente.exists(nombrePod):
        raise HTTPException(status_code=404, detail=f"Pod '{nombrePod}' no encontrado en Redis.")
    
    datosPod = await redisCliente.hgetall(nombrePod)
    if len(datosPod) < 3:
        raise HTTPException(status_code=400, detail=f"No hay suficiente información recibida por las antenas para el pod '{nombrePod}'.")
    datosPodJSON = {antena: json.loads(data) for antena, data in datosPod.items()}

    listaAntenas = []
    for antena in datosPodJSON:
        listaAntenas.append(DatosAntena(name=antena, pod=nombrePod, distance=datosPodJSON[antena]['distance'], metrics=datosPodJSON[antena]['message']))

    datoPod = InfoPod(InfoAntenas(antenas = listaAntenas))
    return await datoPod


@app.get("/revisarRegis/")
async def revisarRedis():
    claves = await redisCliente.keys('*')
    return {"claves_almacenadas": claves}


@app.delete("/podhealth_split/{nombrePod}")
async def EliminarInfoPod(nombrePod: str):    
    if not await redisCliente.exists(nombrePod):
        raise HTTPException(status_code=404, detail=f"Pod '{nombrePod}' no encontrado en Redis.")
    await redisCliente.delete(nombrePod) #Limpiar toda la indformación del Pod