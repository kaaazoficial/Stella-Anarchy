# Anarchy — Cloudflare Worker

Proyecto separado de Stella y Katheryne. Recibe eventos de un bot de comunidad, valida la firma del webhook y genera respuestas con Cloudflare Workers AI.

## Lo que incluye

- Worker independiente con binding `env.AI` (no requiere clave de Z.AI).
- Modelo predeterminado `@cf/zai-org/glm-4.7-flash`.
- Verificación HMAC-SHA256 del cuerpo original y protección contra timestamps antiguos.
- Modo de respuesta directa y modo REST asíncrono opcional.
- Sin base de datos, memoria persistente ni almacenamiento de conversaciones.
- Pruebas automáticas sin llamadas reales a Anarchy o Workers AI.

## Importante antes de conectar el webhook real

La capa de autenticación es configurable, pero los siguientes detalles deben confirmarse con la documentación oficial de Anarchy antes de habilitar tráfico real: nombre del header de firma, header de timestamp, bytes firmados, URL/ruta de envío y esquema de autorización. No se deben copiar estos valores de otra plataforma.

## Cloudflare Workers Builds desde este monorepo

Conecta el repositorio `kaaazoficial/Stella-Anarchy` en Cloudflare Workers y configura:

- **Worker name:** `stella-anarchy` (debe coincidir con el nombre actual del Worker en Cloudflare).
- **Root directory:** `anarchy-worker`
- **Build command:** vacío (no hay compilación requerida).
- **Deploy command:** `npx wrangler deploy`
- **Branch:** `main` después de fusionar el PR.

El archivo `wrangler.jsonc` se encuentra dentro de esta carpeta. La configuración define el binding de Workers AI como `AI`.

## Variables y secretos

No subas secretos al repositorio. En Cloudflare, añade `ANARCHY_WEBHOOK_SECRET` como secret del Worker. El token del bot y los ajustes de API solo serán necesarios si se confirma el modo REST. El secreto real se debe cargar después de contrastar el formato de firma de Anarchy.

## Rutas

- `GET /` estado básico.
- `GET /health` comprobación de salud.
- `POST /webhook` recepción de eventos firmados.

El starter no debe considerarse integrado con Anarchy hasta validar el esquema real de eventos y la respuesta HTTP que espera su webhook.
