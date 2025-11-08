import numpy as np
import json
from requests import FastAPI, pydantic  

# Coordenadas de posicionamiento de las antenas
antena0 = [-500, -200] 
antena1 = [100, -100] 
antena2 = [500, 100] 


def ObtenerPosicionPod(d1, d2, d3):
    posAntena0, posAntena1, posAntena2 = np.array(antena0), np.array(antena1), np.array(antena2) # Las posiciones de las antenas se convierten en arrays de numpy para facilitar los calculos
    # Vectores entre antenas
    ejeX = (posAntena1 - posAntena0) / np.linalg.norm(posAntena1 - posAntena0)
    i = np.dot(ejeX, posAntena2 - posAntena0)
    ejeY = (posAntena2 - posAntena0 - i * ejeX) / np.linalg.norm(posAntena2 - posAntena0 - i * ejeX)
    d = np.linalg.norm(posAntena1 - posAntena0)
    j = np.dot(ejeY, posAntena2 - posAntena0)
    # Cálculo de coordenadas x e y
    x = (d1**2 - d2**2 + d**2) / (2 * d)
    y = ((d1**2 - d3**2 + i**2 + j**2) / (2 * j)) - (i / j) * x
    # Posición final
    posNave = posAntena0 + x * ejeX + y * ejeY
    return round(float(posNave[0]),2), round(float(posNave[1]), 2)


def ObtenerMetricasPod(*mensajes): # @mensajes es una lista de listas
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
        print(vectorMetricas)
        valoresMetricas = [metrica.replace(unidadesMetricasValidas[i], "") for metrica in vectorMetricas]
        print(valoresMetricas)
        for valor in valoresMetricas:
            if valoresMetricas.count(valor) >= 2:  # Si 2 o mas antenas coinciden en el valor de la métrica
                metricasObtenida.append(valor + unidadesMetricasValidas[i])
                break
            else:
                metricasObtenida.append("")
    return metricasObtenida

def main():
    pod ={"nombre": "Pod1", 
          "distancias": [538.5164807134504, 223.606797749979, 412.3105625617661], 
          "infoEstados": [["590C"  ,  "1MWh" , "110C"  ,  ""],
                          ["590C"  ,  ""     , "110C"  ,  "60%"],
                          ["1MWh"  ,  ""     , "60%"   ,  "60%"]]
        }
    
    dataPod = {"nombre": pod["nombre"],
               "ubicacion": ObtenerPosicionPod(*pod["distancias"]),
               "metricas": ObtenerMetricasPod(*pod["infoEstados"])
              }
    

    print(f"Informacion del pod: {json.dumps(dataPod)}")
    return 1
main()