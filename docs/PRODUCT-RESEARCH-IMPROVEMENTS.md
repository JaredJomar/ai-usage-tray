# AI Usage Tray: oportunidades de mejora

Fecha: 2026-10-10

## Alcance

Este informe está limitado a una aplicación de bandeja de Windows que consulta el
estado de uso de Codex y muestra información local. No convierte en requisitos los
problemas de la aplicación Codex/CLI, su interfaz de configuración, la rotación de
cuentas ni los cambios del cliente oficial.

## Qué ya existe

AI Usage Tray ya incluye:

- Tooltip de la bandeja con ventanas de 5 horas y 7 días, porcentajes y cuentas
  regresivas.
- Dashboard con balance, créditos, uso local, actividad reciente e historial.
- Actualización manual y periódica.
- Umbrales de notificación y cooldown.
- Tema claro/oscuro, secciones configurables e inicio con Windows.
- Instalador independiente para Windows y checksum publicado para `v1.0.8`.

Por eso, las oportunidades siguientes son refinamientos de la experiencia de la
bandeja, no implementaciones desde cero.

## Prioridades aplicables al tray

### P0 — Confiabilidad del dato mostrado

1. **Estado de actualización de la cuota**
   - Mostrar en el tooltip y en el dashboard cuándo ocurrió la última consulta
     exitosa de la cuota, separado de la actividad local.
   - Diferenciar entre dato actualizado, caché vigente, caché obsoleta, sesión
     expirada y error de red o de la API.
   - Mantener el último dato útil sin presentarlo como actual cuando la actualización
     falla.

2. **Estado de actualización y autenticación**
   - Hacer visible que `Refresh Now` está en curso, terminó correctamente o falló,
     sin obligar al usuario a revisar los logs.
   - Ofrecer una acción clara para volver a autenticarse cuando el token ya no sea
     válido.

Estas mejoras atacan el riesgo principal de un monitor de bandeja: que un número
antiguo o fallido parezca confiable. Son más relevantes aquí que copiar funciones
internas de la aplicación Codex.

### P1 — Lectura rápida e historial local

3. **Tooltip más informativo**
   - Conservar el formato compacto actual, pero añadir una línea breve de estado:
     `Updated 2m ago`, `Stale`, `Login required` o `API unavailable`.
   - Mantener la cuenta regresiva de ambas ventanas y la semántica actual de colores.

4. **Historial local más útil**
   - Mostrar claramente el periodo cubierto, los huecos de captura y la fecha de
     cada snapshot.
   - Permitir exportar el historial local a CSV o JSON si los usuarios necesitan
     analizar tendencias fuera de la aplicación.
   - No prometer un «total histórico» si la aplicación no estuvo ejecutándose para
     capturar esos datos.

5. **Notificaciones orientadas a la bandeja**
   - Revisar que las alertas de umbral y reinicio no sean repetitivas ni ambiguas.
   - Añadir silenciamiento temporal o una vista previa del motivo de la alerta,
     conservando los umbrales existentes.

### P2 — Diagnóstico y distribución

6. **Diagnóstico local sin exponer secretos**
   - Añadir una vista de diagnóstico con la versión, el proveedor, la última
     actualización, el estado de autenticación y la categoría del error, sin
     tokens ni cabeceras.

7. **Experiencia de actualización**
   - Mantener el checksum y las notas de la versión como parte de cada release.
   - Considerar una comprobación explícita de nuevas versiones, sin actualizar
     silenciosamente.

8. **Accesibilidad de la bandeja y el dashboard**
   - Verificar la escala DPI, el contraste de los estados, la navegación por teclado
     y los tamaños de texto en Windows con un escalado distinto del 100 %.

## Fuentes externas realmente comparables

Estas fuentes se usan solo como referencia de monitores de Windows y bandeja, no
como especificación del producto:

- [codex-usage-tray](https://github.com/alex-indi/codex-usage-tray): monitor de
  Windows con énfasis en el uso local, la privacidad y la presentación en bandeja.
- [codex-usage-monitor](https://github.com/D1NOOO/codex-usage-monitor): monitor de
  Windows comparable en configuración visual, historial y distribución.

Los issues de la aplicación Codex/CLI se excluyeron de la priorización porque
describen problemas del cliente oficial que AI Usage Tray no controla.

## Roadmap recomendado

1. **Primera entrega:** fecha de la última consulta exitosa de la cuota, estado de
   caché obsoleta o error, y feedback visible de actualización y autenticación.
2. **Segunda entrega:** tooltip informativo e historial local con el periodo cubierto.
3. **Tercera entrega:** exportación, notificaciones menos intrusivas y diagnóstico.
4. **Después:** actualización explícita y mejoras de DPI.

La expansión a más proveedores o múltiples cuentas debe tratarse como una decisión
separada; no es una consecuencia necesaria de mejorar la bandeja actual.

## Decisión pendiente

La recomendación es implementar la primera entrega de confiabilidad del dato, pero
no comenzar a modificar código hasta confirmar ese alcance o elegir otra mejora
del documento.
