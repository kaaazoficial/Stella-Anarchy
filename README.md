# Stella — Bot comunitario

Bot comunitario independiente para Anarchy. Este repositorio está reservado para la adaptación de la base reutilizable de Katheryne, eliminando por completo las funciones de Genshin Impact y la identidad, branding y características exclusivas del proyecto original.

## Estado de desarrollo

Repositorio creado. La migración y depuración del código todavía no se han completado; no desplegar esta versión como bot funcional.

## Objetivos de adaptación

- Conservar infraestructura genérica de webhooks, moderación, utilidades y funciones comunitarias que sean independientes del juego.
- Eliminar consultas, bases de datos, comandos, fuentes, guías y referencias a Genshin Impact.
- Sustituir la identidad, personalidad, prompts, nombres internos, almacenamiento y documentación propios de Katheryne.
- Separar las credenciales y la persistencia de Stella de cualquier servicio anterior.
- Ejecutar las pruebas y validaciones antes del primer despliegue.

## Privacidad

Stella no debe crear memoria personal permanente ni conservar historiales conversacionales. La persistencia debe limitarse a datos operativos estrictamente necesarios, con retención definida y eliminación automática.

## Desarrollo

Python 3.11+. Los comandos de instalación y despliegue se documentarán una vez terminada la migración y verificados los requisitos.
