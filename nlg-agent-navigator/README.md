# FlexLife Conversational Agent

React 19 and FastAPI proof of concept built on Microsoft Agent Framework. The application owns orchestration, canonical conversation state, policies, and structured responses. Foundry supplies a knowledge agent, bounded underwriting and language specialists, and Large and Small model deployments behind one user-facing conversation.

## Configure Foundry

Copy `backend/.env.example` to `backend/.env` and set:

```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com/api/projects/your-project
FOUNDRY_API_KEY=your-project-api-key
FOUNDRY_AGENT_KB=KnowledgeBase
FOUNDRY_MODEL_LARGE=NLG-Large
FOUNDRY_MODEL_SMALL=NLG-Small
UNDERWRITING_AGENT=NLG-Underwriting
LANGUAGE_AGENT=NLG-Language
FOUNDRY_API_VERSION=v1
LOG_PATH=C:\Data\NLG\Traces
PROMPT_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/nlg_agent
```

`PROMPT_DATABASE_URL` is required. Startup creates the dedicated `prompt_library`
PostgreSQL schema and loads every required selected version once for that process.
A missing URL, unavailable database, schema error, or missing selected version stops
startup; there is no runtime prompt file and no fallback to repository prompts.

Populate prompts through Prompt Studio or with the explicit one-time importer:

```powershell
cd backend
.venv\Scripts\python -m prompt_studio_api.import_prompts path\to\manifest.json
```

The manifest may contain prompt text directly or reference a source file for import.
Source files are only importer inputs and are not copied or required by the application.

For local development, run `docker compose up -d prompt-db` from the project root.
The plain PostgreSQL service uses port `5433`, so it can run beside the RAG demo on
port `5432`; the prompt library does not require pgvector.

Prompt Studio is a separate FastAPI process on port `8001`. It owns prompt-management,
trace-query, and replay APIs; the agent API on port `8000` does not mount those routes.
Both processes share `backend/.env` and PostgreSQL, with prompt definitions in the
`prompt_library` schema and trace/replay records in the `prompt_studio` schema. Vite
proxies `/prompt-api` to the Studio process while `/api` continues to target the agent.

The application uses one controlled orchestration path and canonical conversation history. FlexLife product and substantive unmatched questions use the grounded Knowledge agent, which connects supporting general knowledge to FlexLife or declines unrelated requests. Only greetings, navigation, and clarification use the Small model. Underwriting requests follow `Underwriting evidence -> Large composition -> Language validation -> deterministic policy gate`. Communication-sensitive answers also receive Language validation; routine requests avoid that extra call.

The Underwriting and Language agents are bounded specialists, not autonomous peers. The application chooses when to invoke them, owns the final response contract, requires citations for underwriting and product claims, and escalates when evidence or required validation is unavailable.

Named Foundry agents have no portal-managed instructions. Foundry rejects its
`instructions` request field when an agent reference is present, so the application
places the selected database prompt in a clearly delimited local request envelope.
Model-deployment calls continue to use the framework's local instructions field.

`LOG_PATH` is optional and specifies the trace root. When set, each session receives its own hashed directory under that root, and every UI turn is written there as a self-contained Markdown file. The trace records the ordered application routing decisions, exact prompts and responses for every agent/model call, citations, policy checks, revisions, timing, failures, and the final UI response. Application-owned instructions are included; Foundry-managed configuration that is not exposed to the application is identified as unavailable rather than inferred.

Trace files contain verbatim conversation, prompt, response, citation, and exception content. They intentionally exclude API keys and transport headers, but may still contain sensitive customer or underwriting information. Store traces in a protected location, restrict access, and apply an appropriate retention policy. Common repository-local `traces` directories are ignored by Git, but an external protected path is recommended.

Example layout:

```text
C:\Data\NLG\Traces\
└── session-0f3a...\
	├── turn-0001-20261006T143210.123456Z-a1b2....md
	└── turn-0002-20261006T143245.654321Z-c3d4....md
```

Without the project endpoint, API key, both model deployments, and all three specialist agent names, the API starts in a clearly labeled demo mode that makes no FlexLife product claims.

## Run

### Run the containerized application

The production-style local stack packages the compiled frontend, Agent API, and
Prompt Studio API in one application container. PostgreSQL remains a separate
container so its named volume can persist independently of application rebuilds.

Copy `backend/.env.example` to `backend/.env`, configure the Foundry values, then
run from the repository root:

```powershell
docker compose up --build
```

Open `http://localhost:8080`. Stop the stack without deleting PostgreSQL data:

```powershell
docker compose down
```

The `nlg-agent_prompt-db-data` volume is preserved. Do not add `--volumes` unless
you intend to delete the local database.

### Run the development servers

Backend:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Frontend, in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite development server proxies `/api` to `http://localhost:8000`.

### Debug in VS Code

After installing the backend and frontend dependencies above, open **Run and Debug** and select
`Full stack: UI + Backend`. This starts FastAPI under the Python debugger, starts the Vite
development server, and opens the UI in an Edge debugging session. Breakpoints in both Python
and React/TypeScript are supported.

## Validate

```powershell
cd backend
.venv\Scripts\python -m pytest -q

cd ..\frontend
npm run lint
npm run build
```
