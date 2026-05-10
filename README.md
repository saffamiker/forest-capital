# Forest Capital Portfolio Intelligence System

MSFA FNA 670 Graduate Practicum — Queens University of Charlotte  
Partner: Forest Capital

## Research Question
Does diversification across equities and fixed income — via static or dynamic asset allocation — improve risk-adjusted performance relative to a 100% equity benchmark?

## Architecture
Six AI agents (Claude Opus CIO, four Claude Sonnet specialists, Google Gemini Pro independent analyst) plus a QA agent that audits all results before presentation.

## Quick Start

### Backend
```bash
cd backend
python -m venv venv
source venv/Scripts/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # Fill in your API keys
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

Visit http://localhost:5173 — you will be prompted to log in via magic link.  
In development mode the magic link prints to the backend terminal.

## Sprint Status
- [x] Sprint 1 — Frontend shell + skeleton FastAPI + magic-link auth
- [ ] Sprint 2 — Data layer + statistical tests
- [ ] Sprint 3 — Agents + council debate
- [ ] Sprint 4 — Cross-validation + QA audit
- [ ] Sprint 5 — Report generation + deployment
