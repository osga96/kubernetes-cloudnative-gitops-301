# M02-02 — Optimización de imágenes Docker

[← Página anterior](M02-01-adaptacion-cloudnative.md) · [Siguiente página →](../M03-kubernetes-desarrolladores/README.md)

> Práctica del módulo. Lee primero el [README del módulo](README.md) (sección *Imágenes Docker: monolito vs multistage*).

## Objetivo

Construir y entender una imagen **multistage** más segura que el Dockerfile monolítico, y **medir** diferencias de tamaño, capas y usuario de ejecución.

## Prerrequisitos

- M02-01 completado (API con config en variables de entorno).
- Haber leído en el README la tabla *Anti-patrón vs buena práctica*.

## Antes de empezar — restaurar punto de partida

```bash
./scripts/lab-prepare.sh m02-02
```

Deja `api.py` en estado M02-01 y `Dockerfile` **monolítico** (como al terminar M02-01). Si ya tenías multistage en el repo, este paso te devuelve al ejercicio.

Comprueba:

```bash
grep -c 'AS builder' infra/app/api/Dockerfile || echo "sin multistage (correcto al empezar)"
```

## En qué consiste

No se trata solo de «hacer la imagen más pequeña». Partirás del Dockerfile monolítico, **implementarás** la versión multistage, y **medirás** diferencias de tamaño, capas y usuario de ejecución.

## Mapa del ejercicio

```text
Paso 1      Entender Dockerfile.legacy (referencia fija)
Paso 2      Implementar Dockerfile multistage en infra/app/api/Dockerfile
Paso 3      Medir con image-size-compare.sh
Paso 4–5    Verificar no-root y funcionalidad
Paso 6      Caché: requirements.txt antes que api.py
```

| Al terminar… | Deberías poder explicar… |
|--------------|---------------------------|
| Paso 2 | Qué se descarta del stage `builder` |
| Paso 4 | Por qué root en contenedor es un riesgo |
| Paso 6 | Por qué un cambio en `api.py` no reinstala pip |

---

### 1 — Baseline: Dockerfile monolítico

**Acción:** Abre `infra/app/api/Dockerfile.legacy`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py .
EXPOSE 8081
CMD ["python", "api.py"]
```

**Por qué:** Necesitas una **línea base** antes de optimizar. Este Dockerfile es legible y válido para labs; en producción suele ser el primer candidato a refactor.

**En profundidad:** Todo ocurre en **una sola imagen final**:

- Las capas de `pip install` permanecen.
- El proceso corre como **root** (usuario por defecto de la imagen base).
- No hay separación entre «lo necesario para construir» y «lo necesario para ejecutar».

**Resultado esperado:** Identificas que build y runtime comparten la misma imagen — no hay stage intermedio descartado.

---

### 2 — Implementar el multistage

**Acción:** Sustituye el contenido de `infra/app/api/Dockerfile` (ahora monolítico) por un build de **dos stages**:

| Stage | Nombre | Qué hace |
|-------|--------|----------|
| 1 | `builder` | `pip install --prefix=/install` |
| 2 | `runtime` | Copia `/install`, copia `api.py`, `USER app` |

Patrón objetivo:

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY api.py .
USER app
EXPOSE 8081
ENV PORT=8081
CMD ["python", "api.py"]
```

**Por qué:** Solo el stage **`runtime`** se publica como imagen final. El `builder` existe durante el build y se descarta.

> [!TIP]
> Si te atascas, compara con `infra/solutions/Dockerfile.m02-02` **después** de intentarlo. No copies sin entender cada línea.

**Resultado esperado:** `./scripts/lab-verify.sh m02-02` responde OK.

---

### 3 — Comparar tamaños y capas

**Acción:** Tras implementar el multistage, reconstruye y mide:

```bash
./scripts/lab-down.sh
./scripts/lab-up.sh
./scripts/image-size-compare.sh
```

**Por qué:** Los números anclan la conversación. A veces el ahorro en MB es modesto con bases `slim`; las **capas** y el **usuario** siguen siendo argumentos sólidos.

**En profundidad — interpretar la tabla:**

| Métrica | Qué te dice |
|---------|-------------|
| Tamaño (MB) | Espacio en disco / transferencia registry |
| Capas (aprox.) | Complejidad del historial; más capas ≠ siempre peor |
| Usuario runtime | root vs `app` — impacto en seguridad |

**Resultado esperado:** Tabla similar a:

```text
| legacy     | ~135 MB | root  |
| multistage | ~128 MB | app   |
```

Los valores exactos dependen de tu entorno; anótalos para el reto.

---

### 4 — Verificar usuario no-root

**Acción:**

```bash
./scripts/lab-up.sh
docker compose -f infra/docker-compose.yml exec demo-api id
docker compose -f infra/docker-compose.yml exec demo-api whoami
```

**Por qué:** Cualquier plataforma que ejecute contenedores (Kubernetes, ECS Fargate, Container Apps, etc.) penaliza o desaconseja procesos root. Comprobarlo en la imagen evita rework al desplegar.

**En profundidad:** Si entraras al contenedor legacy (root), un proceso comprometido podría escribir en más rutas del filesystem del contenedor. Con `app`, la superficie es menor.

**Resultado esperado:**

```text
uid=10001(app) gid=10001(app) groups=10001(app)
app
```

---

### 5 — Validar que la optimización no rompe la app

**Acción:**

```bash
curl -s http://127.0.0.1:8081/work | jq .
curl -s http://127.0.0.1:8081/ready | jq .
docker compose -f infra/docker-compose.yml exec demo-api ps aux | head -5
```

**Por qué:** Toda optimización debe preservar comportamiento. Multistage mal copiado (`/install`) es un error clásico: la app arranca pero falla al importar módulos.

**Resultado esperado:** `/work` y `/ready` OK; el proceso `python api.py` aparece en la lista de procesos bajo usuario `app`.

---

### 6 — Orden de capas y caché de build

**Acción:** Observa el orden en `Dockerfile`:

1. `COPY requirements.txt` + `RUN pip install` (capa que cambia poco).
2. `COPY api.py` (capa que cambia a menudo).

**Por qué:** Docker reutiliza capas cacheadas. Si primero copias todo el código y luego instalas dependencias, **cada cambio en una línea de `api.py` invalida pip install** → builds CI lentos en M05.

**En profundidad — simulación mental:**

```text
Cambio solo en api.py  →  rebuild desde COPY api.py  →  pip CACHE HIT
Cambio en requirements →  rebuild desde pip install   →  pip CACHE MISS
```

> [!TIP]
> En CI (M05), combinar este orden con `--cache-from` en GitHub Actions reduce minutos de pipeline.

**Resultado esperado:** Entiendes por qué `requirements.txt` va **antes** que el código fuente en Dockerfiles profesionales.

---

### 7 — Verificar tu trabajo

**Acción:**

```bash
./scripts/lab-verify.sh m02-02
```

**Resultado esperado:** `OK: Dockerfile cumple los requisitos de M02-02`.

---

## Recapitulación

| Tema | Legacy | Multistage (M02) |
|------|--------|------------------|
| Stages | 1 | 2 (builder + runtime) |
| Usuario | root | app (10001) |
| Caché pip | Mezclada con código | Capa independiente |
| Uso en curso | Solo comparación | Imagen estándar del lab y despliegues posteriores |

## Comprueba tu entendimiento

**Imagen activa en Compose**

```bash
docker compose -f infra/docker-compose.yml config | grep -A2 "demo-api:" -m1
```

→ Build context `app/api` con `dockerfile: Dockerfile` (no `.legacy`).

**Seguridad en runtime**

```bash
docker compose -f infra/docker-compose.yml exec demo-api whoami
```

→ `app`.

## Reto

### 1 — Informe de optimización

Rellena con tu salida de `image-size-compare.sh`:

| Métrica | legacy | multistage |
|---------|--------|------------|
| Tamaño (MB) | 49.6 | 46.4 |
| Capas (aprox.) | 16 | 18 |
| Usuario runtime | root | app (10001) |

**Pregunta extra:** ¿Qué beneficio te parece más importante aquí — MB ahorrados, no-root o caché de capas? Justifica en una frase.

--> 1º no-root para mayor seguridad, 2º MB ahorrados porque si crece la aplicacion, se notará más, 3º cache de capas

<details>
<summary>Ver orientación</summary>

Con `python:3.12-slim`, MB suele ser secundario (~5–10 MB). **No-root** y **separación builder/runtime** suelen ser la respuesta más sólida en entrevistas y diseño real.

</details>

## Errores frecuentes

| Síntoma | Causa probable | Cómo arreglarlo |
|---------|----------------|-----------------|
| `bc: command not found` | Falta `bc` en el Codespace | `sudo apt-get update && sudo apt-get install -y bc` |
| `ModuleNotFoundError: flask` | Mal copiado `/install` | Revisa `COPY --from=builder /install /usr/local` |
| Permiso denegado al escribir | Proceso `app` en ruta solo-lectura | Usa volúmenes o almacenamiento gestionado en despliegue cloud |
| Imagen legacy en Compose | `dockerfile` mal indicado | Debe ser `Dockerfile`, no `Dockerfile.legacy` |

→ Siguiente módulo: **[M03 — Kubernetes para desarrolladores](../M03-kubernetes-desarrolladores/README.md)**
