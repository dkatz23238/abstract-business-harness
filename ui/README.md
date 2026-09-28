# Analysis agent web UI

Lightweight AG-UI frontend for bizharness. One unified feed of conversation,
reasoning, and tool activity. Title, placeholder, and the API-call banner
label come from `GET /profile`.

```
npm install
npm run dev
```

Point `VITE_API_URL` at the engine (default `http://localhost:8811`). In
dev the browser calls same-origin paths; Vite proxies them to that origin.

The app asks for the API key printed by `bizharness serve` and sends it
on every API call. The Admin tab still uses `HARNESS_ADMIN_TOKEN`
separately, and can rotate the API key.
