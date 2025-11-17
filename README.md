Este repositorio contiene el proyecto Carrera de PODs v1.0. El mismo cuenta con su docuemntación y diagrama de arquitectura incluido. 
El proyecto esta hosteado en GCP y para accder al Swagger y probarlo se debe ingresar a http://136.110.196.219/docs
Tambien es posible descargar el proyecto y correrlo de forma local ejecutando los siguientes pasos:

1)	Instalar librerías necesarias: pip install –m fastapi, uvicorn[standard], numpy, Pydantyic, typing, json
2)	Levantar redis en un contenedor de docker: docker run --name redis-local -p 6379:6379 -d redis
3)	Ejecutar API: fastapi dev main.py
