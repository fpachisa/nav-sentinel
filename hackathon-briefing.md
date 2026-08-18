# AI Agent Briefing: "All Things Agentic" Hackathon Context & Guidelines

This document serves as a comprehensive, structured briefing designed for an autonomous AI agent to understand, plan, and execute a project submission for the **All Things Agentic Hackathon**. It outlines the core theme, target tracks, mandatory technical constraints, rules, evaluation criteria, submission requirements, and available resources.

---

## 1. Hackathon Meta-Information

| Parameter | Details |
| :--- | :--- |
| **Hackathon Name** | All Things Agentic Hackathon |
| **Theme** | Build next-generation, autonomous AI agents that run in the background, handle massive datasets, and automate complex workflows asynchronously. |
| **Host / Manager** | Google (Managed by Devpost) |
| **Total Cash Prize Pool** | $180,000 USD |
| **Submission Deadline** | August 31, 2026 @ 5:00 PM PDT (8:00 PM EDT) |
| **Target Audience for this Doc** | Autonomous AI Agent (System Context) |

---

## 2. Core Theme: Defining the "Next-Generation Agent"

The core philosophy of this hackathon is to move beyond passive, static chatbots that wait for user prompts. The goal is to build **autonomous agents** capable of:
1. **Taking a Goal**: Accepting a high-level user objective.
2. **Making a Plan**: Decomposing that objective into logical execution steps.
3. **Executing Asynchronously**: Actively pulling information, making decisions, manipulating data pipelines, and completing multi-step tasks in the background without constant hand-holding.
4. **Solving Real Friction**: Actively removing everyday friction at work, at home, or within an enterprise environment.

---

## 3. Mandatory Technical Stack Constraints

To qualify for prizes, every project submitted **must** strictly adhere to the following architecture requirements:

1. **Core Language Model**:
   * Must use **Gemini 3.5** (or newer).
   * Must be accessed through either the **Gemini API** or **Google Cloud Vertex AI**.
2. **Agent Development Framework**:
   * Must use at least **one** of the following Google Agent Frameworks:
     * **Google Agent Development Kit (ADK)**
     * **Google GenAI SDK**
     * **Antigravity SDK**
     * **GenKit**
3. **Google Cloud Infrastructure**:
   * Must integrate at least **one** Google Cloud infrastructure service for backend, database, or ingestion. Examples include:
     * **Cloud Run** (highly recommended for serverless deployment)
     * **Cloud SQL**
     * **Firestore**
     * **Google Kubernetes Engine (GKE)**
     * **Pub/Sub** (excellent for asynchronous event-driven triggers)
4. **Deployment & Cost Rules**:
   * The application **does not** need to remain publicly active or live during the judging period (to prevent unnecessary hosting costs).
   * You must, however, provide clear, undeniable proof in the submission video and codebase that it was built and successfully deployed on Google Cloud (e.g., displaying the Google Cloud Console, Cloud Run dashboards, or Vertex AI logs).

---

## 4. Hackathon Tracks (Categories)

Every submission must align with exactly **one** of the following three tracks. Select the track that matches the core design of your system:

### Track A: The Taskmaster
* **Objective**: Build a complete, action-oriented workflow instead of a text-only chatbot.
* **Core Focus**: Identify a tedious, messy, multi-step chore in a professional, academic, or personal setting. The agent must handle the execution details, move structured data across appropriate channels, perform heavy background lifting, and prove it can solve the problem end-to-end autonomously.

### Track B: The Collaborative Partner
* **Objective**: Build an interactive agent that works dynamically alongside a human user.
* **Core Focus**: Rather than just outputting a single response, the agent must guide the user step-by-step. It should ask clarifying questions when appropriate, take notes, and feature a robust feedback loop to capture user critiques and dynamically adapt its reasoning to the user's thought patterns.

### Track C: The Fortified Enterprise Fleet
* **Objective**: Build a highly scalable, secure network of institutional agents integrated into official enterprise infrastructure.
* **Core Architectural Components**:
  * **Discovery & Lifecycle (Agent Registry)**: A centralized repository for publishing, versioning, and discovering enterprise-approved agents.
  * **Core Execution & State**:
    * **Agent Runtime**: For handling long-running, asynchronous background processes.
    * **Memory Bank**: For secure, persistent, cross-session storage of context and historical state over extended timelines.
  * **Security & Governance**:
    * **Agent Identity**: Zero-trust access control protocols.
    * **Agent Gateway**: Unified routing and compliance/policy enforcement.
    * **Model Armor**: Inline guardrails designed to detect and block prompt injection, tool poisoning, and PII leaks.
  * **Telemetry (Agent Observability)**: OpenTelemetry-compliant logs and end-to-end reasoning chain traces to audit agent decision-making.

---

## 5. Official Submission Checklist

To ensure a valid submission before the **August 31, 2026 @ 5:00 PM PDT** deadline, the agent must compile and package the following deliverables:

- [ ] **Track Category**: Selection of one primary track (Taskmaster, Collaborative Partner, or Fortified Enterprise Fleet).
- [ ] **Hosted URL (Optional but highly encouraged)**: A web UI, Chrome Extension, or mobile app URL for judges to test.
- [ ] **Technical Description**:
  * Detailed text overview of the problem solved.
  * Highlighted features and core functionality.
  * Comprehensive list of technologies and Google Cloud services used.
  * Information on other data sources integrated.
  * Documented findings, challenges, and learnings.
- [ ] **Code Repository URL**:
  * A GitHub, GitLab, or Bitbucket link containing the complete, auditable codebase.
  * *Note*: If the repository is kept private, access must be shared with `testing@devpost.com` and `cloudhackathons@google.com`.
- [ ] **Spin-up Instructions**:
  * A clear, step-by-step guide in the project's `README.md` file explaining how to run the project locally or deploy it to the cloud to ensure reproducibility.
- [ ] **Architecture Diagram**:
  * A clear visual representation showing how the system components connect (e.g., how Gemini interfaces with the backend, database, frontend, and external APIs).
- [ ] **~4-Minute Demo Video**:
  * Highlighting the specific problem solved and the value proposition.
  * Featuring a live, unedited demonstration of the agent in action.
  * **Mandatory**: Must show visible, concrete proof that the backend is running on Google Cloud (e.g., showing the Cloud Run dashboard, Cloud Console billing, or Vertex AI active logs).

### Optional Bonus Points Checklist
- [ ] **Public Content Creation**: Publish a public blog post, podcast, or video (on Medium, dev.to, YouTube, etc.) detail-mapping the build process. Must include explicit language indicating that the content was created for this hackathon.
- [ ] **Social Media Promotion**: Share an update on X, LinkedIn, Instagram, or Facebook using the hashtag `#AllThingsAgenticHackathon`.
- [ ] **Multimodal Model Integration**: Incorporate additional Google AI models like **Gemma**, **Veo**, or **Lyria** into the system.

---

## 6. Evaluation & Judging Rubric

Judges will score projects based on three equally weighted categories:

```
┌─────────────────────────────────────────────────────────────┐
│                   HACKATHON JUDGING RUBRIC                  │
├──────────────────────────────┬──────────────────────────────┤
│ Innovation & Operational     │ - Autonomous, high-value     │
│ Utility (40%)                │   actions over chat loops    │
│                              │ - Level of friction removed  │
├──────────────────────────────┼──────────────────────────────┤
│ Architectural Discipline &  │ - Solid engineering choices │
│ Tech Stack (30%)             │ - State/memory preservation  │
│                              │ - Decoupled systems & safety │
├──────────────────────────────┼──────────────────────────────┤
│ Demo & Production            │ - Clear, live demo video     │
│ Readiness (30%)              │ - Reproducible setup docs    │
│                              │ - Proof of Google Cloud build│
└──────────────────────────────┴──────────────────────────────┘
```

1. **Innovation & Operational Utility (40%)**:
   * Evaluates the degree of real-world friction removed. Rewards autonomous actions, decision-making, and heavy lifting over simple, passive conversational interfaces.
2. **Architectural Discipline & Tech Stack (30%)**:
   * Assesses engineering robustness. Checks system decoupling, state/memory management, credential security, error handling, and production-mindset over quick, brittle scripts.
3. **Demo & Production Readiness (30%)**:
   * Focuses on proof of execution. Requires a clean, reproducible README setup, an architecture diagram, and explicit visual confirmation of Google Cloud deployment.

---

## 7. Developer Resources & Operational Support

To assist in rapid prototyping while keeping developer costs near zero:
1. **Google Cloud Trial & Credits**:
   * Sign up for a no-cost Google Cloud trial.
   * Apply for **$150 in Google Cloud Credits** using the credit allocation form on the Devpost Resources tab.
2. **Starter Materials**:
   * Access beginner guides under the Resources tab to understand agent fundamentals.
   * Utilize official templates for building agents with Google's **Agent Development Kit (ADK)**.
3. **Cost Optimization Tips**:
   * Design systems defensively to keep usage charges minimal (e.g., using low-cost tier services, utilizing free tiers, and tearing down resource-heavy nodes when not in use).
   * Since live deployment at the exact time of judging is not required, resources can be stopped/scaled down after capturing video and code proof.

---

## 8. Detailed Prize Pool

A total of **$180,000** is awarded across various categories:

* **Grand Prize (1 Winner)**: $50,000 USD cash + $5,000 Google Cloud credits + Virtual coffee with a Google team member + Social promotion.
* **Track Winners (3 Winners - 1 per track)**:
  * **The Taskmaster**: $20,000 cash + $2,000 Google Cloud credits + Google coffee + Social promo.
  * **The Collaborative Partner**: $20,000 cash + $2,000 Google Cloud credits + Google coffee + Social promo.
  * **The Fortified Enterprise Fleet**: $20,000 cash + $2,000 Google Cloud credits + Google coffee + Social promo.
* **Startup Excellence (1 Winner)**: $20,000 cash + $5,000 Google Cloud credits + Google coffee + Social promo.
  * *Eligibility*: Must be submitting on behalf of an incorporated organization and provide a corporate email address.
* **Individual/Hobbyist - Best Team/Solo (2 Winners)**: $10,000 cash + $1,000 Google Cloud credits + Google coffee + Social promo each.
* **Best Architectural Design (2 Winners)**: $5,000 cash + $1,000 Google Cloud credits each.
* **Best Multimodal UX (2 Winners)**: $5,000 cash + $1,000 Google Cloud credits each.
* **Honorable Mentions (5 Winners)**: $2,000 cash + $500 Google Cloud credits each.

---

## 9. AI Agent Action Plan

For an autonomous agent consuming this file, execute the following workflow:

```
  Step 1: Track Alignment
  ├── Analyze project idea against tracks (Taskmaster, Partner, Fleet)
  └── Select the single most suitable category

  Step 2: Architecture & Constraints Check
  ├── Set LLM to Gemini 3.5 (API/Vertex AI)
  ├── Integrate a Google Agent Framework (e.g., ADK or GenKit)
  └── Choose Google Cloud Service (e.g., Cloud Run for serving)

  Step 3: Setup & Verification
  ├── Claim $150 credit and set up Google Cloud project
  └── Maintain reproducible configuration inside local files

  Step 4: Repository & Documentation Build
  ├── Draft clean, step-by-step README.md spin-up instructions
  └── Draw a detailed system architecture diagram

  Step 5: Video & Repo Packaging
  ├── Create demo script highlighting problem, UX, and Google Cloud proof
  └── Ensure repo is public or shared with testing@devpost.com and cloudhackathons@google.com
```
