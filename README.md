# Browser Automation Agent (VLM Support)
A robust browser automation agent powered by Vision-Language Models (VLM). This system intelligently navigates web pages, handles various types of popups, extracts page state, and makes decisions autonomously to complete user tasks.

## Architecture Overview
The system flow relies on a central agent loop that continuously evaluates page state via Playwright, processes it through OpenAI, and executes necessary actions while handling popups and recovery fallbacks.

```mermaid
flowchart TD
	U[User task] --> M[main.py
Load task + start URL]
	M --> B[Playwright Browser]
	B --> P[Popup Handler]

	P --> C1[Cookie Consent
Auto-accept]
	P --> C2[Location / Delivery
Auto-dismiss]
	P --> C3[Irrelevant Popup
Auto-dismiss or Escape]
	P --> A1[Auth Wall
Try login fallback]
	P --> H1[Dangerous Confirmation
Ask human]

	C1 --> W[Wait for page readiness]
	C2 --> W
	C3 --> W
	A1 --> W
	H1 --> W

	W --> S[Screenshot + Accessibility Tree]
	S --> L[agent_loop.py
Send to OpenAI]
	L --> D{Next Action}

	D -->|click| X1[actions.py
click_element]
	D -->|type| X2[actions.py
type_into_field]
	D -->|scroll| X3[actions.py
scroll_down]
	D -->|navigate| X4[actions.py
navigate_to]
	D -->|done| Z[Finish]
	D -->|stuck| T[Stop]

	X1 --> R[Recheck popup + continue]
	X2 --> R
	X3 --> R
	X4 --> R

	R --> P
	R --> L

	subgraph Recovery
		F1[Popup Classification]
		F2[Auth Fallback]
		F3[Action Fallbacks]
		F4[Logging]
	end

	P -.-> F1
	A1 -.-> F2
	X1 -.-> F3
	X2 -.-> F3
	M -.-> F4
	L -.-> F4
```
