## Approach
- Read existing files before writing. Don't re-read unless changed.
- Thorough in reasoning, concise in output.
- Skip files over 100KB unless required.
- No sycophantic openers or closing fluff.
- No emojis or em-dashes.
- Do not guess APIs, versions, flags, commit SHAs, or package names. Verify by reading code or docs before asserting.

## Reparto con Codex

El plugin de Codex está instalado. El trabajo se reparte así.

Te quedas tú (Claude):
- Entender el problema y preguntar lo que falte.
- Planear los pasos antes de tocar archivos.
- Decidir la arquitectura y los límites de cada cambio.
- Revisar todo lo que vuelva de Codex.

Se le pasa a Codex, con el subagente codex-rescue y sin esperar a que
te lo pida:
- Construcción repetitiva y larga.
- Refactors grandes que tocan muchos archivos.
- Errores atorados que ya se intentaron una vez.

Reglas fijas:
- Nada de lo que vuelve de Codex se da por bueno sin revisar.
- Si Codex falla dos veces en la misma tarea, la tarea regresa a ti.
- Delegar no es desentenderse: dime qué pediste y qué volvió.
