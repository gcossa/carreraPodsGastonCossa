import numpy as np
import json
import os
import httpx # Importado para hacer llamadas HTTP a los jurados
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request
from typing import Union 
import redis.asyncio as redis
from google.cloud import tasks_v2 # Importado para Cloud Tasks
from google.protobuf import timestamp_pb2
import logging

# ------------------- Variables ---------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
PROJECT_ID = os.getenv("PROJECT_ID", "carrera-pods-gcossa")
QUEUE_ID = os.getenv("QUEUE_ID", "jurados-pods") 
LOCATION_ID = os.getenv("LOCATION_ID", "us-central1")                                
SERVICE_URL = os.getenv("SERVICE_URL", "https://carrera-pods-api-794893099209.us-central1.run.app") # Utilizada por Cloud Tasks para llamar al worker

# Lista de los 3 servicios de jurados
urlsJurados = [
    "https://jurado.uno.com/notificar",
    "https://jurado.dos.com/notificar",
    "https://jurado.tres.com/notificar"
]

# Cliente de Cloud Tasks
http_client = httpx.AsyncClient()
tasks_client = None
task_queue_path = None
try:
    tasks_client = tasks_v2.CloudTasksClient()
    task_queue_path = tasks_client.queue_path(PROJECT_ID, LOCATION_ID, QUEUE_ID)
except Exception as e:
    logger.error(f"No se pudo inicializar el cliente de Cloud Tasks: {e}")
try:
    redisCliente = redis.Redis(host = os.getenv("REDIS_HOST", "localhost"), 
                port = 6379, 
                db = 0, 
                decode_responses = True)
except:
    print("No se pudo conectar con el servidor de Redis")
    redisCliente = None

# ------------------------- Funciones -------------------------------
async def ObtenerPosicionPod(distancias, antenasNombres):
    if not redisCliente:
        raise HTTPException(status_code=503, detail="Servicio de Redis no disponible (cálculo de posición)")
    logger.info(f"Buscando antenas en Redis: {antenasNombres}")
    try:
        posicionesAntenas = await redisCliente.hmget("antenas", *antenasNombres) #Posiciones de las antenas elegidas ej: ["[250, 250]", "[500, 250]", "[250, 500]"]
        if not posicionesAntenas or None in posicionesAntenas:
             logger.error(f"No se encontraron todas las antenas ({antenasNombres}) en Redis. Datos: {posicionesAntenas}")
             raise HTTPException(status_code=404, detail=f"No se encontraron todas las antenas ({antenasNombres}) en la configuración de Redis.")
        posAntena0 = np.array(json.loads(posicionesAntenas[0]))
        posAntena1 = np.array(json.loads(posicionesAntenas[1]))
        posAntena2 = np.array(json.loads(posicionesAntenas[2]))
        logger.info(f"Posiciones de antenas cargadas desde Redis.")
    except Exception as e:
        logger.error(f"Error al leer/parsear configuración de antenas desde Redis: {e}")
        raise HTTPException(status_code=500, detail=f"Error al leer configuración de antenas de Redis: {e}")
    for d in distancias:
        if d < 0:
            raise HTTPException(status_code=400, detail="Las distancias no pueden ser negativas")
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
        metricaDeterminada = False
        for valor in valoresMetricas:
            if valoresMetricas.count(valor) >= 2:  # Si 2 o mas antenas coinciden en el valor de la métrica se considera ese valor
                metricasObtenida.append(valor + unidadesMetricasValidas[i])
                metricaDeterminada = True
                break
        if metricaDeterminada == False:
            metricasObtenida.append("")
    return metricasObtenida

# -----------------  Modelos Pydantic -----------------
app = FastAPI()

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

class Jurado(BaseModel):
    urlJurado: str # La URL del jurado externo al que llamar
    payload: dict  # El resultado que se le envia

class Antena(BaseModel):
    name: str
    position: list[int, int]

 # ------------------ FastAPI Endpoints ---------------------
@app.post("/podhealth/")
async def InfoPod(data: InfoAntenas):
    distancias = [antena.distance for antena in data.antenas]
    antenasNombres = [antena.name for antena in data.antenas]
    posicionPodx, posicionPody = await ObtenerPosicionPod(distancias, antenasNombres)
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

@app.post("/tasks/notificarJurado")
async def notify_juror_worker(task: Jurado, request: Request): 
    logger.info(f"Worker: Recibida tarea para notificar a {task.urlJurado}")
    try:
        response = await http_client.post(task.urlJurado, json=task.payload, timeout=10.0) # Se intenta hacer el POST al jurado externo
        response.raise_for_status() #Se lanza error a Cloud Task para que sepa que debe reintentar la tarea
        logger.info(f"Worker: Notificación a {task.urlJurado} exitosa (HTTP {response.status_code}).")
        return {"status": "success", "juror_response": response.text} # Devuelvo un 200 OK a Cloud Tasks para que marque la tarea como completada
    except httpx.TimeoutException:
        logger.warning(f"Worker: Timeout al contactar a {task.urlJurado}. Reintentando...")
        raise HTTPException(status_code=504, detail="Timeout del jurado externo") # Devuelvo un error 504 para que Cloud Tasks reintente
    except httpx.RequestError as e:
        logger.warning(f"Worker: Error de conexión al contactar a {task.urlJurado}. Reintentando... Error: {e}")
        raise HTTPException(status_code=503, detail=f"Error de conexión del jurado externo: {e}") # Devuelvo un 503 para que Cloud Tasks reintente
    except httpx.HTTPStatusError as e:
        logger.warning(f"Worker: El jurado {task.urlJurado} respondió con error HTTP {e.response.status_code}. Reintentando...")
        raise HTTPException(status_code =503, detail=f"El jurado devolvió un error: {e.response.text}") # Devolvemos el mismo error que el jurado para que Cloud Tasks reintente

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
    datoPod = await InfoPod(InfoAntenas(antenas = listaAntenas))
    if not tasks_client or not task_queue_path:
        logger.error("El cliente de Cloud Tasks no está inicializado. No se encolarán tareas.")
    else:
        tasks_creadas = []
        for urlJurado in urlsJurados:
            try:
                task_payload = Jurado(urlJurado = urlJurado, payload = datoPod).model_dump_json() # Payload que Cloud Tasks enviará al worker
                # Tarea que Cloud Tasks ejecutará
                task = tasks_v2.Task(
                    http_request=tasks_v2.HttpRequest(
                        http_method=tasks_v2.HttpMethod.POST,
                        url=f"{SERVICE_URL}/tasks/notificarJurado", 
                        headers={"Content-Type": "application/json"},
                        body=task_payload.encode('utf-8')
                    )
                )
                created_task = tasks_client.create_task(parent=task_queue_path, task=task)
                tasks_creadas.append(created_task.name)
            except Exception as e:
                logger.error(f"Error al crear la tarea para {urlJurado}: {e}")
                tasks_creadas.append(f"ERROR: {e}")
        
        logger.info(f"Tareas encoladas para {nombrePod}: {tasks_creadas}")
        datoPod["notificacion_jurados"] = {
            "status": "Tareas de notificación encoladas.",
            "tasks_info": tasks_creadas
        }
    return  datoPod

@app.get("/datosCapturadosPod/{nombrePod}")
async def RevisarRedis(nombrePod: str):
    datosCapturadosParaPod = await redisCliente.hgetall(nombrePod)
    return {"Pod": nombrePod,
            "DatosCapturados": datosCapturadosParaPod}

@app.post("/registrarAntena/")
async def RegistrarAntena(antena: Antena):
    if not redisCliente:
        raise HTTPException(status_code=500, detail="No se pudo conectar con el servidor de Redis")
    if await redisCliente.hexists("antenas", antena.name):
        raise HTTPException(status_code=400, detail=f"La antena '{antena.name}' ya está registrada.")
    await redisCliente.hset("antenas", antena.name, json.dumps(antena.position)) #Ej: {"antenas"->"antena0"->"position": [250, 250]}
    return  {"message" : f"Datos de antena {antena.name} almacenados ",
             "data": await redisCliente.hgetall("antenas")}
       
@app.delete("/eliminarAntena/{nombreAntena}")
async def EliminarAntena(nombreAntena: str):    
    if not await redisCliente.hexists("antenas", nombreAntena):
        raise HTTPException(status_code=404, detail=f"Antena '{nombreAntena}' no encontrada.")
    await redisCliente.hdel("antenas", nombreAntena) #Limpiar toda la indformación del Pod
    await redisCliente.hdel("antenas", nombreAntena) #Limpiar toda la indformación del Pod
    
    return {"message": f"Antena '{nombreAntena}' eliminada de Redis."}

@app.delete("/podhealth_split/{nombrePod}")
async def EliminarInfoPod(nombrePod: str):    
    if not await redisCliente.exists(nombrePod):
        raise HTTPException(status_code=404, detail=f"Pod '{nombrePod}' no encontrado en Redis.")
    await redisCliente.delete(nombrePod) #Limpiar toda la indformación del Pod
    return {"message": f"Información del Pod '{nombrePod}' eliminada de Redis."}