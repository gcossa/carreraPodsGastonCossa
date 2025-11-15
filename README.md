flowchart TD

subgraph Clientes
    U[Usuarios / Clientes]
end

U --> LB[Cloud Load Balancer]
LB --> ARMOR[Cloud Armor (WAF)]
ARMOR --> RUN[Cloud Run<br>FastAPI - Carrera Pods]

RUN -->|POST/GET| VPCSC[Serverless VPC Access Connector]
VPCSC --> REDIS[Memorystore for Redis<br>Estado de antenas]

RUN -->|Encola 3 tareas| TASKS[Cloud Tasks<br>juror-queue]
TASKS --> J1[Jurados externos<br>/tasks/notify_juror]

subgraph DevOps
    CB[Cloud Build<br>gcloud builds submit]
    REG[Artifact / Container Registry<br>Imagen Docker]
end

CB --> REG
REG --> RUN
