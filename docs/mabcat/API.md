# REST API — quick reference with examples

Interactive OpenAPI docs: `http://<host>:8000/api/docs`. All endpoints except login take
`Authorization: Bearer <token>`.

```bash
H=http://localhost:8000/api/v1
TOKEN=$(curl -s $H/auth/login -H 'Content-Type: application/json' \
        -d '{"email":"admin@mabix.local","password":"admin"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Bearer $TOKEN"

# accounts (admin)
curl -s $H/users -H "$AUTH" -H 'Content-Type: application/json' -d '{"email":"teacher@school.in","role":"teacher","password":"change-me-please"}'
curl -s $H/users -H "$AUTH" -H 'Content-Type: application/json' -d '{"email":"parent@home.in","role":"parent","password":"change-me-please"}'

# parent: child, consent, robot
curl -s $H/students -H "$PARENT" -H 'Content-Type: application/json' -d '{"display_name":"Anu","grade":"5","age":10,"preferred_language":"ml"}'
curl -s -X PUT $H/students/$SID/consent -H "$PARENT" -H 'Content-Type: application/json' -d '{"learning":true,"audio":true}'
curl -s $H/devices -H "$PARENT" -H 'Content-Type: application/json' -d '{"device_id":"mabcat-001","student_id":"'$SID'"}'   # → device_token (once)

# teacher: upload curriculum (PDF/DOCX/PPTX/TXT/images), inspect, search
curl -s $H/curricula -H "$TEACHER" -F title="Grade 5 Science" -F files=@chapter3.pdf -F files=@worksheet.jpg
curl -s $H/curricula/$CID -H "$TEACHER"                                   # status + tree
curl -s "$H/curricula/$CID/search?q=what%20is%20chlorophyll" -H "$TEACHER" # RAG hits with filename/page

# lesson plans: generate → edit → approve
curl -s -X POST $H/nodes/$NODE/plans -H "$TEACHER"
curl -s -X PATCH $H/plans/$PLAN -H "$TEACHER" -H 'Content-Type: application/json' -d '{"status":"approved"}'

# materials: flashcards | worksheet | quiz | illustration | video | story_video
curl -s -X POST "$H/nodes/$NODE/materials?kind=quiz" -H "$TEACHER"

# classes & assignment
curl -s -X POST "$H/classes?name=5A" -H "$TEACHER"                                     # → join_code
curl -s -X POST "$H/classes/join?join_code=AB12CD34&student_id=$SID" -H "$PARENT"
curl -s -X POST "$H/assignments?student_id=$SID&curriculum_id=$CID" -H "$TEACHER"

# progress, sessions, transcripts, analytics
curl -s $H/students/$SID/progress -H "$PARENT"
curl -s $H/students/$SID/sessions -H "$PARENT"
curl -s $H/sessions/$SESSION/transcript -H "$PARENT"
curl -s $H/classes/$CLASS/analytics -H "$TEACHER"

# speech
curl -s $H/stt/status -H "$AUTH"
curl -s $H/stt/transcribe -H "$AUTH" -F file=@question.wav -F language=ml-en      # original language preserved
curl -s $H/stt/analyse-text -H "$AUTH" -F text='Photosynthesis എന്താണ്?'
curl -s $H/tts/synthesize -H "$AUTH" -F text='Hello Anu!' -F language=en -o hello.wav

# health
curl -s http://localhost:8000/api/v1/health
```

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST/GET /users` (admin) |
| Students | `GET/POST /students`, `PATCH/DELETE /students/{id}`, `PUT /students/{id}/consent`, `GET /students/{id}/progress`, `/sessions`, `/assignments` |
| Classes | `POST/GET /classes`, `POST /classes/join`, `GET /classes/{id}/analytics` |
| Devices | `POST/GET /devices`, `PATCH/DELETE /devices/{id}`, `POST /devices/{id}/rotate-token` |
| Curriculum | `POST/GET /curricula`, `GET/DELETE /curricula/{id}`, `POST /curricula/{id}/documents`, `GET /curricula/{id}/search`, `GET/PATCH /nodes/{id}` |
| Plans | `POST /nodes/{id}/plans`, `PATCH /plans/{id}` |
| Materials | `POST /nodes/{id}/materials?kind=`, `GET /materials/{id}`, `GET /materials/{id}/file` |
| Assignments | `POST /assignments` |
| Speech | `GET /stt/status`, `POST /stt/transcribe`, `POST /stt/analyse-text`, `GET /stt/benchmark`, `GET /tts/status`, `POST /tts/synthesize` |
| Admin | `GET /analytics/overview`, `GET /audit`, `GET /health` |
| WebSocket | `/ws/mabcat/{device_id}`, `/ws/mabcat/audio/{device_id}` — see WEBSOCKET_PROTOCOL.md |
