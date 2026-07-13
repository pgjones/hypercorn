# Benchmarks

Suite de benchmarks mantenible para Hypercorn. Su funcion es validar cambios de rendimiento con una metodologia reproducible y suficientemente estable como para vivir en `main`.

## Objetivos

- medir mejoras y regresiones reales en rutas criticas;
- comparar ramas o refs contra un baseline reproducible;
- separar benchmarks generales de escenarios dirigidos;
- priorizar resultados robustos frente a cifras puntuales.

## Metodologia por defecto

Todos los comparadores `benchmarks/compare*.py` siguen estas reglas por defecto:

- intercalan runs de `current` y `baseline`;
- resumen por medianas de run;
- exponen `mean`, `median` y `p95`;
- dejan `--sequential` solo para diagnostico o compatibilidad historica.

Esto reduce sesgos por orden de ejecucion, calentamiento, drift del sistema y ruido de la maquina.

## Estructura

- `benchmarks/app.py`
  App ASGI minima usada como objetivo de los benchmarks de servidor.
- `benchmarks/_runtime.py`
  Infraestructura compartida de servidor, TLS, readiness, puertos y percentiles.
- `benchmarks/_compare.py`
  Infraestructura compartida de worktrees, intercalado, resumen y salida JSON.
- `benchmarks/run_load.py`
  Benchmark dirigido de HoL en HTTP/2.
- `benchmarks/fragmented_body.py`
  Benchmark dirigido de request body H2 muy fragmentado.
- `benchmarks/general.py`
  Benchmark general para `/fast` y otras rutas HTTP.
- `benchmarks/ws.py`
  Benchmark de eco WebSocket.
- `benchmarks/h3.py`
  Benchmark real de HTTP/3 sobre QUIC con `aioquic`.
- `benchmarks/task_group.py`
  Microbenchmark de `TaskGroup.spawn_app()`.
- `benchmarks/compare*.py`
  Comparadores contra otro ref o repo.

## Escenarios disponibles

### HoL HTTP/2

Una sola conexion HTTP/2, un stream lento y varios streams rapidos multiplexados. Mide si existe bloqueo global a nivel de conexion.

```bash
python -m benchmarks.run_load
python -m benchmarks.compare --baseline-ref upstream/main
```

### Body fragmentado HTTP/2

Ejercita `QueuedStream` y el coste de entregar muchos `DATA` pequenos al app ASGI.

```bash
python -m benchmarks.fragmented_body
python -m benchmarks.compare_fragmented_body --baseline-ref upstream/main
```

### Benchmark general HTTP

Sirve para medir el camino rapido sin mezclarlo con escenarios artificiales.

```bash
python -m benchmarks.general --http-version 1.1
python -m benchmarks.general --http-version 1.1 --tls
python -m benchmarks.general --http-version 2
python -m benchmarks.compare_general --baseline-ref upstream/main
```

El comparador general ejecuta:

- HTTP/1.1 sin TLS
- HTTP/1.1 con TLS
- HTTP/2 con TLS

### WebSocket echo

Valida handshake y eco binario con payload configurable.

```bash
python -m benchmarks.ws --tls
python -m benchmarks.compare_ws --baseline-ref upstream/main --tls
```

### HTTP/3 real

Mide QUIC/H3 real con una conexion H3 multiplexada y un cliente `aioquic`.

```bash
python -m benchmarks.h3
python -m benchmarks.compare_h3 --baseline-ref upstream/main
```

### TaskGroup

Microbenchmark de investigacion para separar el coste fijo de `TaskGroup.spawn_app()` del servidor completo.

```bash
python -m benchmarks.task_group --mode asgi
python -m benchmarks.task_group --mode wsgi
python -m benchmarks.compare_task_group --mode asgi --baseline-ref upstream/main
python -m benchmarks.compare_task_group --mode wsgi --baseline-ref upstream/main
```

## Comandos habituales

Comparar contra `upstream/main`:

```bash
python -m benchmarks.compare_general --baseline-ref upstream/main --runs 6
python -m benchmarks.compare --baseline-ref upstream/main --runs 6
python -m benchmarks.compare_h3 --baseline-ref upstream/main --runs 4
```

Comparar contra un repo ya existente sin crear worktree:

```bash
python -m benchmarks.compare_general --baseline-path /ruta/a/otro/repo --runs 6
```

Guardar resultados en JSON:

```bash
python -m benchmarks.compare_general \
  --baseline-ref upstream/main \
  --runs 6 \
  --output-json benchmarks/results/general.json
```

## Guia de interpretacion

- `p95` es el guard-rail principal de cola larga.
- `mean` y `median` ayudan a distinguir mejora general de mejora puntual.
- `req/s` o `messages/s` sirven para leer throughput, pero no deben ocultar empeoramientos claros de latencia.
- Los benchmarks dirigidos como HoL o body fragmentado validan cambios estructurales.
- Los benchmarks generales detectan si una optimizacion local rompe el balance global.

## Evidencia de batching de salida

Medicion local del 12 de julio de 2026 contra `06f2d41`, con ejecuciones intercaladas y
resumen por mediana de run. Los porcentajes positivos indican mejora respecto al baseline.

La implementacion medida aplica tres politicas relacionadas, pero conserva las fronteras de
cada protocolo:

- asyncio escribe inmediatamente y ejecuta `drain()` al alcanzar 64 KiB o una barrera de
  conexion;
- Trio agrupa la escritura en un buffer FIFO acotado a 64 KiB;
- H3 admite operaciones no terminales hasta 32 KiB y espera el flush real en `END_STREAM`.

### Camino rapido

| Escenario | Runs | Throughput | Media | p95 |
| --- | ---: | ---: | ---: | ---: |
| HTTP/1.1 `/fast` | 6 | +0,32 % | +0,38 % | +2,54 % |
| HTTP/1.1 TLS `/fast` | 6 | +1,68 % | +1,67 % | +1,82 % |
| HTTP/2 `/fast` | 6 | +5,20 % | +5,11 % | +14,89 % |
| HTTP/3 `/fast` | 6 | +40,99 % | +29,30 % | +30,73 % |
| WebSocket 1 KiB | 6 | +3,62 % | +3,48 % | +0,73 % |
| WebSocket 64 KiB | 6 | +0,40 % | +0,39 % | +1,46 % |

### Respuesta de 1 MiB

La ruta usada fue `/large-response?chunks=64&chunk_size=16384`.

| Escenario | Runs | Throughput | Media | p95 |
| --- | ---: | ---: | ---: | ---: |
| HTTP/1.1 | 4 | +5,79 % | +4,76 % | +7,04 % |
| HTTP/1.1 TLS | 4 | +2,48 % | +2,09 % | +4,20 % |
| HTTP/2 | 4 | +0,87 % | -1,22 % | +0,08 % |
| HTTP/3 | 8 | -3,84 % | +2,54 % | +5,21 % |

El limite H3 de 32 KiB prioriza p95 y backpressure. En esta carga grande acepta una perdida de
throughput del 3,84 % para evitar las regresiones de cola larga observadas con limites de 48 y
64 KiB. El camino rapido H3 conserva una mejora amplia.

### Correcciones del harness

- los servidores de benchmark usan `--workers 0` para medir el worker sin el supervisor
  multiproceso;
- H3 usa un bind TCP efimero y evita depender de que `127.0.0.1:8000` este libre;
- WebSocket espera `message_finished=True` y valida el tamano completo del eco antes de cerrar
  una muestra.

Comandos principales usados:

```bash
python -m benchmarks.compare_general --baseline-ref 06f2d41 --no-fetch --runs 6
python -m benchmarks.compare_h3 --baseline-ref 06f2d41 --no-fetch --runs 6 \
  --total-requests 400 --warmup-requests 50 --concurrency 40
python -m benchmarks.compare_ws --baseline-ref 06f2d41 --no-fetch --runs 6 \
  --payload-size 65536
```

## Mantenimiento

- Antes de aceptar un cambio de rendimiento, medir contra un baseline estable.
- No usar una sola pasada secuencial para sacar conclusiones.
- Si un escenario es nuevo, anadir primero un benchmark reproducible y luego el cambio.
- Mantener la app de benchmark y los comparadores pequenos, explicitamente documentados y sin dependencias ocultas.

## Notas

- La suite usa `benchmarks/app.py` como app objetivo.
- `tests/assets/cert.pem` y `tests/assets/key.pem` habilitan TLS y ALPN para H2.
- Los resultados son comparaciones relativas entre ramas o refs, no numeros absolutos publicables.
