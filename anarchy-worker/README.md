# Stella · Cloudflare Workers AI Gateway

Este Worker es un proveedor de inferencia privado para Stella. No es un segundo bot de Anarchy y no recibe webhooks de salas.

## Arquitectura

`Anarchy → Stella (Render) → este Worker → env.AI.run(GLM-4.7-Flash)`

Stella mantiene su identidad, personalidad, memoria, comandos, permisos y lógica de conversación. Render envía el prompt completo a `POST /internal/infer`; el Worker ejecuta el modelo mediante el binding `AI` y devuelve un objeto compatible con OpenAI Chat Completions para que el motor actual de Stella lo procese.

## Cloudflare

- Worker: `anarchy-worker`
- Directorio raíz: `anarchy-worker`
- Build command: vacío
- Deploy command: `npx wrangler deploy`
- Rama de producción tras fusionar el PR: `main`
- Binding: Workers AI con nombre `AI`
- Modelo por defecto: `@cf/zai-org/glm-4.7-flash`

El binding `env.AI` no requiere una API key de Cloudflare. La única credencial adicional es un secreto compartido propio para impedir que terceros utilicen el endpoint público.

## Configuración de secretos

Crea un secreto de Worker llamado `STELLA_GATEWAY_TOKEN`. En Render configura:

- `CLOUDFLARE_AI_GATEWAY_URL=https://anarchy-worker.jeanblanco774.workers.dev/internal/infer`
- `CLOUDFLARE_AI_GATEWAY_TOKEN`: exactamente el mismo valor del secreto del Worker.
- `STELLA_AI_PRIMARY_PROVIDER=cloudflare` para usar Cloudflare como proveedor principal.

No uses una API key de Cloudflare ni publiques este token en GitHub. Conserva Gemini, Groq y Z.AI como proveedores de respaldo.

## Rutas

- `GET /health`: comprueba que el Worker y sus bindings estén presentes.
- `POST /internal/infer`: endpoint privado autenticado para inferencia.
- Las demás rutas devuelven 404. No se debe configurar este Worker como webhook de Anarchy.

Las solicitudes están limitadas por tamaño, número de mensajes, roles y tokens de salida. El modelo no se puede seleccionar desde Render: el Worker usa el valor controlado por `AI_MODEL`.

## Pruebas

```bash
npm test
npm run check
```
