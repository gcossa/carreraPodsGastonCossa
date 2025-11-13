import numpy as np
import os
import redis.asyncio as redis
import json
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from typing import Union 
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
                raise HTTPException(status_code=404, detail="No se pudo obtener una métrica válida")
    return metricasObtenida


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
    await redisCliente.hset(f"Pod: {data.pod}", antena_name, mapping = {"distance": data.distance,"message": json.dumps(data.message)}) #Ej: {"Pod":"Anakin Skywalker": {"antena0":{"distance": 250.5, "message": ["590C", "60%", "110C"]}}}
    return  {"message" : f"Datos de antena {antena_name} almacenados en Redis para Pod '{data.pod}'",
             "data": await redisCliente.hgetall(data.pod)}


@app.get("/podhealth_split/{nombrePod}")
async def ObtenerInfoPod(nombrePod: str):
    # 1. Verificar si el Pod existe (si la clave Hash existe)
    if not await redisCliente.exists(nombrePod):
        raise HTTPException(status_code=404, detail=f"Pod '{nombrePod}' no encontrado en Redis.")
    
    #######################################################################
    # 2. Obtener todos los campos (antenas) y valores del Hash
    # Esto devuelve un dict: {b'antena0': b'{"distance":...}', b'antena1': ...}
    datosGuardados = await redisCliente.hgetall(f"Pod:{nombrePod}")

    
    return datosGuardados
    # 3. Verificar si tenemos los 3 fragmentos necesarios
    if len(datosGuardados) < 3:
        raise HTTPException(
            status_code=400,
            detail=f"Datos incompletos para el Pod '{nombrePod}'. Solo se han recibido {len(datosGuardados)}/3 fragmentos."
        )

    # 4. Preparar los datos para las funciones de lógica
    nombreAntenas = ['antena0', 'antena1', 'antena2']
    distancias = []
    metricas_fragmentos = []
    
    for nombreAntena in nombreAntenas:
        claveAntena = nombreAntena.encode('utf-8') # Como los datos se guardaron en bytes, hay que codificar el string para buscar la clave correcta TODO: Analizar si es necesario
        if claveAntena not in datosGuardados:
             raise HTTPException(status_code=400, detail=f"Falta el fragmento clave: {nombreAntena}")
        data = json.loads(datosGuardados[claveAntena]) # Deserializamos el string JSON (que está en bytes) a un dict de Python
        distancias.append(data['distance'])
        metricas_fragmentos.append(data['message'])
        
    # 5. Procesar y devolver la respuesta
    posicionPodx, posicionPody = ObtenerPosicionPod(distancias)
    metricas_finales = ObtenerMetricasPod(metricas_fragmentos)
    
    response_data = {
        "pod": pod_name,
        "position": {"x": posicionPodx, "y": posicionPody},
        "metrics": metricas_finales
    }
    
    # Opcional: Limpiar el Hash de Redis después de procesar
    # await redis_client.delete(pod_name)
    
    #return response_data

    ################################################################

    
    for key in infoAntenas:
        listaAntenas. append(DatosAntena(name=key, pod = infoAntenas[key]['pod'], distance = infoAntenas[key]['distance'], metrics = infoAntenas[key]['message']))
    data = InfoPod(InfoAntenas(antenas = listaAntenas))
    return await data
    


