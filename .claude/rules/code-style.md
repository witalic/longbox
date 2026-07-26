# Code style

Loaded every session (global rule — no `paths`).

- Conversation with the owner is **Ukrainian**; code, comments, UI copy and ALL documentation are
  **English** (README included — no exceptions).
- Python ≥ 3.11 with type hints; Vue 3 `<script setup lang="ts">` + strict TS
  (`noUnusedLocals` is on — unused imports fail the type-check).
- Match the surrounding code's idiom, naming, and comment density. Comments state constraints
  the code can't show — never narrate what the next line does or why a change is correct.
- No backward-compatibility shims, dead code, unused exports, or leftover CSS in landed code.
- Shared logic lives in one place: frontend domain helpers in `data.ts`
  (`groupByNum`, `metaOf`, `chapterRowsOf`, `READ_COLOR`, `coverAt`), suggestions in `store.ts`,
  value cleanup in `normalize.ts`. Don't re-implement these inline.
