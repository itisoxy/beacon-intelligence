
Beacon Intelligence is a prototype investment intelligence application built for the Beacon Capital Stewardship assessment.
My approach to the build was deliberately problem first rather than technology first.
I tend to work through projects using:
--------------------------------------
Problem → Solution → System → Delivery
--------------------------------------

So I didn't start by choosing an AI model and then looking for somewhere to use it.
I started by looking at the funds, the type of user I was designing for, the information they would need and the questions they would realistically want answered.

My approach

 Start with the fund and the user
 ---------
The first thing I looked at was the size and structure of the funds in the supplied data.
From there I looked at the types of interfaces people working with larger enterprise and institutional portfolios are already familiar with.

The UI was influenced by platforms such as Salesforce, alongside institutional investment platforms such as BlackRock Aladdin.
I wasn't trying to recreate either product. 

I wanted Beacon to feel familiar:
clear navigation
portfolio-level summaries
drill-down reporting
research surfaced rather than buried
evidence behind important numbers
the ability to move from a high-level question into deeper analysis
I wanted the result to feel closer to an internal investment intelligence product than a dashboard built purely for an assessment.

Use reporting experience to shape the analytics layer
----------
   
I also drew on my previous experience building and pulling reports in Salesforce when deciding how to segment Beacon's analytics layer.

That experience had already made me used to thinking about reporting through different dimensions, filters and levels of detail rather than treating a dataset as one large table.
I carried that mindset into Beacon.

It influenced the way I segmented the data around:
fund
reporting period
benchmark
performance
excess return
allocation
manager
cash flow
quarterly history
supporting evidence
It also influenced the UX.
Rather than showing everything at once, I wanted the user to be able to start at portfolio level and progressively drill into the part of the data that mattered.

Work backwards from the UX
------------------

I worked backwards from the experience I wanted the user to have.
Before designing the AI architecture, I thought about the kinds of questions a CIO or investment team would most likely ask.
For example:
How did the fund perform?
Did it outperform its policy benchmark?
Where are we drifting from policy?
Is that drift getting worse?
Which managers deserve attention?
What happened with cash flows?
What changed during the year?
How does BPT compare with BLE?
What should I investigate next?
Can I see the evidence behind that conclusion?
Those questions helped define the three main areas of Beacon.
Portfolio
The factual reporting layer.
Insights
The research layer, identifying things in the supplied data that may deserve attention.
Ask Beacon
The conversational layer, allowing the user to interrogate both the portfolio data and the research using natural language.
So the build broadly worked backwards like this:



Understand the fund and user
            ↓
    Design the experience
            ↓
 Identify likely CIO questions
            ↓
 Structure the analytics layer
            ↓
  Merge the supplied datasets
            ↓
 Build research / Insight logic
            ↓
   Design the AI architecture
            ↓
    Choose the technology
            ↓
        Deliver
```
Bringing the supplied data together
Once I knew the questions I wanted Beacon to answer, I worked backwards into the supplied datasets.
The workbooks were ingested, validated and normalised into a canonical analytical layer.
This brought the different datasets together around common investment concepts such as:
fund
reporting period
performance
benchmark
excess return
AUM
cash flow
asset allocation
policy allocation
allocation drift
manager performance
quarterly history
A key principle was avoiding separate calculations for Portfolio, Insights and Ask Beacon.
Where possible, all three work from the same underlying data.
I also retained provenance back to the original workbook, sheet, row and cells where available.
That means an answer can be traced back to supporting data rather than existing only as an AI-generated statement.
From problem to system
Once the problem, UX and analytical structure were clearer, I moved into the technical architecture.
The principle I settled on was:
AI should decide what to investigate.
Deterministic code should establish what is true.
The overall Beacon system became:
```text
Excel source data
        ↓
Ingestion + validation
        ↓
Python / DuckDB canonical layer
        ↓
  Portfolio metrics
        ↓
Research / Insight objects
        ↓
  Typed Beacon tools
        ↓
  AI orchestration
        ↓
 Structured responses
        ↓
Portfolio / Insights / Ask Beacon
```
The model is therefore not responsible for calculating the portfolio.
It is there to understand the user, resolve context and decide what information or action is required.
Python and DuckDB establish the underlying numerical result.
How the AI architecture evolved
The AI architecture changed during the build as I learnt more about the interaction patterns and where the model was actually adding value.
I didn't treat the first architecture as fixed.
I used it to prove the agentic interaction first, and then moved more numerical and presentation responsibility into the deterministic application layer.
Architecture 1 — More model-led
My first approach was more heavily centred around the agent and model.
The flow was:
```text
User
  ↓
LangGraph
  ↓
LLM / Ollama
  ↓
Tool selection
  ↓
Python / DuckDB
  ↓
Tool result
  ↓
LLM synthesis
  ↓
Ask Beacon response
```
This was useful initially because it allowed me to prove that a natural-language question could trigger a real analytical action.

The agent could:
```text
Understand question
        ↓
   Select tool
        ↓
   Execute tool
        ↓
  Inspect result
        ↓
Decide whether more
information is required
        ↓

 Generate answer
```
The advantage was flexibility.
The downside was that too much responsibility still sat with the model.
Even when the application already had the correct structured result, the model could be involved again to interpret, format and reconstruct the final answer.
That introduced:
additional latency
more dependency on model quality
larger prompts
unnecessary inference for simple questions
more opportunity for presentation inconsistency
This became particularly noticeable while working with a relatively small local model.
Architecture 2 — AI orchestration + deterministic intelligence
The second architecture separated those responsibilities more clearly.
```text
User
  ↓
Intent + conversational context
  ↓
LangGraph / LLM
  ↓
Typed Beacon tool
  ↓
Python / DuckDB
  ↓
Validated structured result
  ↓
Response type
  ↓
Deterministic UI card / table
  ↓
Optional conversational synthesis
```
The important shift was:
Architecture 1 asked the model to help construct the answer.
Architecture 2 asks the model to decide what analysis is required while the application constructs the factual answer from validated data.
That creates a clearer separation of responsibilities.

AI

Understand question
        ↓
Resolve conversation context
        ↓
Decide what information is needed
        ↓
   Select tool
        ↓
Decide whether another step is required
        ↓
Explain / connect results where useful
```
Python / DuckDB
```text
Retrieve
   ↓
Calculate
   ↓
Compare
   ↓
Rank
   ↓
Validate
   ↓
Return provenance
```
Research objects
```text
Observation
   ↓
Supporting metrics
   ↓
Interpretation
   ↓
Why it matters
   ↓
Possible explanations
   ↓
Limitations
   ↓
What to check next
   ↓
CIO question
```
UI
```text
Structured response type
        ↓
  Known UI component
        ↓
 Card / table / trend
        ↓
      Evidence
        ↓
Contextual follow-up
```
The overall architecture is closer to:

```
                    ┌─────────────────────┐
                    │        USER         │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Intent + Context    │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ LangGraph / LLM     │
                    │ Reason + choose     │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Typed Beacon Tool   │
                    └──────────┬──────────┘
                               ↓
            ┌─────────────────────────────────┐
            │       Python / DuckDB           │
            │ Calculate • Validate • Retrieve │
            └────────────────┬────────────────┘
                             ↓
            ┌─────────────────────────────────┐
            │ Structured Intelligence Object  │
            │ Metrics • Research • Evidence   │
            └────────────────┬────────────────┘
                             ↓
         ┌───────────────────┴───────────────────┐
         ↓                                       ↓
┌─────────────────┐                     ┌─────────────────┐
│    Insights     │                     │   Ask Beacon    │
│ Research view   │                     │ Conversation    │
└─────────────────┘                     └─────────────────┘
```
This was the architecture I preferred because both interfaces can ultimately work from the same underlying intelligence.
Why I preferred the second architecture
For an investment use case, the numerical output needs to be dependable.
I would rather have the model decide:
> I need BPT's FY2026 fund performance and benchmark-relative return.
and then call a deterministic tool, rather than give the model large amounts of portfolio data and ask it to calculate the answer itself.
It also creates a cleaner route to:
caching
faster responses
richer UI
evidence and provenance
model/provider swapping
conversational state
automated testing
The model can improve without changing the underlying calculation layer.
> ...................
Technology choices
> .................
The AI architecture went through several iterations during development.

I initially modelled the agent workflow using LangGraph.

This helped make the individual steps of the agent explicit and gave me a structure for:
tool calling
state
multi-step analysis
clarification
conversational context
For the local implementation I used:
Python for business logic, analytics and Beacon tools
DuckDB for deterministic querying
LangGraph for agent orchestration and state
Ollama for local model inference
Using Ollama locally meant I could build and test the AI workflow without being dependent on an external model provider.
It also helped expose where the model was useful and where normal application logic was a better solution.
For example, the preferred flow became:

``
User asks question
        ↓
AI identifies required analysis
        ↓
   Beacon tool executes
        ↓
Python / DuckDB establishes result
        ↓
Structured response returned
        ↓
     UI renders answer
```
Rather than:
```text
Give entire dataset to LLM
        ↓
Ask LLM to calculate answer
        ↓
Ask LLM to create table
        ↓
Render free-text result
```
Insights
The Insights layer came from another question I worked backwards from:
> \*\*If I was the CIO, what would I actually want someone to bring to my attention?\*\*
I didn't want Insights to simply repeat the Portfolio numbers.
The research layer therefore tries to structure signals around:
what happened
supporting evidence
what it could mean
why it matters
possible explanations
limitations
what should be checked next
a question for the CIO or investment team
One thing I was particularly conscious of was separating fact from inference.
For example:
> \*\*FACT\*\*
> Allocation remained away from policy across several periods.
>
> \*\*POSSIBLE EXPLANATION\*\*
> This may reflect liquidity management or delayed deployment.
>
> \*\*LIMITATION\*\*
> The supplied portfolio data does not establish management intent.
That distinction is important when using AI around investment data.
Ask Beacon
Ask Beacon is the conversational layer over the portfolio and research.
I wanted it to behave more like an analyst conversation than a search box.
For example:
User
> What was BPT's FY2026 return?
Beacon
> On BPT's FY2026 return: BPT performed slightly above the policy benchmark, with a small excess return.
>
> The net cash flow was negative, suggesting a net outflow from the fund.
The user can then ask:
> Compare with BLE.
without needing to repeat the fund and period.
The same applies to follow-ups such as:
Why?
What about managers?
Has that worsened?
The worst one?
Relative to benchmark.
And BLE?
Show me the numbers.
Source?
The intended flow is:
```text
Initial question
      ↓
Resolved context
      ↓
    Answer
      ↓
Natural follow-up
      ↓
Reuse existing context
      ↓
Continue analysis
```
Rather than treating every new message as an isolated search.
Structured responses
Another important design decision was separating reasoning from presentation.
Ask Beacon can return structured response types such as:
`fund\_performance`
`fund\_comparison`
`research\_signals`
`manager\_ranking`
`allocation\_drift`
`allocation\_history`
`cash\_flow`
`source\_evidence`
`clarification`
The flow becomes:
```text
AI determines required analysis
        ↓
Tool returns validated data
        ↓
   response\_type
        ↓
Frontend chooses component
        ↓
Card / table / evidence
```
The model doesn't need to invent the financial presentation itself.
Development and deployment
The main application was developed locally first.
That allowed me to iterate quickly across:
data ingestion
Python analytics
DuckDB
research logic
agent tools
LangGraph state
Ollama behaviour
UI
conversational flows
Once the main system was working, I connected the repository through Git and deployed the web application through Vercel.
The development flow became:

LOCAL DEVELOPMENT

Source files
     ↓
Python / DuckDB
     ↓
  LangGraph
     ↓
   Ollama
     ↓
 Beacon UI

     ↓

Git / GitHub

     ↓

HOSTED DELIVERY

   Vercel
     ↓
Beacon web application
```
For a more complete production deployment I would move toward:
```text
Vercel UI
    ↓
Hosted Beacon API
    ↓
Hosted tool-calling model
    ↓
Python Beacon tools
    ↓
Canonical analytical layer
```
I would still keep Ollama as a useful local development option.
What I would do differently
Use the snapshot feature much earlier
One of the biggest things I would change is how I used the snapshot approach described in the assessment prompt while developing locally.
I understood the supplied data as point-in-time snapshots, but I didn't fully use that feature as part of my development workflow.
If I started again, I would establish a known-good analytical snapshot almost immediately.


Supplied source snapshot
        ↓
   Validate once
        ↓
  Canonical snapshot
        ↓
  Save / version locally
        ↓
Use fixed snapshot for development
```
Then:
```text
             CANONICAL SNAPSHOT
                     │
        ┌────────────┼────────────┐
        ↓            ↓            ↓
    Portfolio     Insights    Ask Beacon
```
This would have meant that while developing:
UI
agent behaviour
conversational state
research cards
tool calling
structured responses
I wouldn't repeatedly need to touch or regenerate the underlying data.
I would also keep a lightweight snapshot manifest containing:
source file
reporting period
ingestion timestamp
schema/version
checksum
validation status
That would make each development state reproducible.
Use snapshots as development checkpoints
I would also have used snapshots or known-good checkpoints much more deliberately while iterating locally.
For example:
```text
SNAPSHOT 1
Canonical data validated
        ↓
SNAPSHOT 2
Portfolio working
        ↓
SNAPSHOT 3
Insights working
        ↓
SNAPSHOT 4
Ask Beacon tool calling working
        ↓
SNAPSHOT 5
Conversational state working
        ↓
SNAPSHOT 6
Final UI / deployment
```
This would have sped up development significantly.
If an experiment with Ask Beacon broke something, I could immediately return to the last known-good state rather than tracing changes across several layers.
It also would have reduced the risk of improving one part of the system while unintentionally regressing another.
This is probably the biggest practical development lesson I would take from the exercise.
Structured result caching
Another improvement would be caching commonly requested structured results.
For example:

fund\_performance | BPT | FY2026

fund\_comparison | BPT | BLE | FY2026

manager\_ranking | BPT | Q4

allocation\_history | BPT | Cash

research\_signals | BPT | FY2026
```
The agent would still determine what information it needs.
The application simply wouldn't repeat deterministic work that had already been completed.
Model and provider evaluation
Ollama was useful for local development because it gave me a self-contained development environment.
The trade-off was latency and weaker reasoning on some more complicated conversational follow-ups.
With another iteration I would run the exact same Beacon tools against several stronger hosted tool-calling models.
Because the model and analytical layers are separated, this can be done without rebuilding the underlying product.
Wider conversational testing
I would also expand the multi-turn test suite.
Particularly around journeys such as:
```text
BPT
 ↓
BLE
 ↓
FY2026
 ↓
Q3
```
and:
```text
Research signal
      ↓
    Why?
      ↓
What about managers?
      ↓
The worst one?
      ↓
Has that worsened?
      ↓
    Source?
```
This would help catch broken conversational loops earlier in development.
What I'm most pleased with
The part I'm probably most pleased with is that the product came together from the problem rather than the technology.
My process was:
```text
Problem
   ↓
Understand the user
   ↓
Design the experience
   ↓
Identify the important questions
   ↓
Structure the data
   ↓
Build the analytical layer
   ↓
Build the research layer
   ↓
Design the AI architecture
   ↓
Select the technology
   ↓
Deliver
```
Rather than:
```text
Choose AI model
      ↓
Find something for it to do
```
For Beacon, that ultimately became:
```text
AI
Interprets + decides what to investigate

                ↓

Python / DuckDB
Calculates + validates what is true

                ↓

Research objects
Add investment context

                ↓

Beacon UI
Turns it into something a CIO can interrogate
```
That separation of responsibilities is the main architectural decision I would carry forward from the prototype.
Questions to try
Performance
> What was BPT's FY2026 return?
Then:
> Compare with BLE.
> Relative to benchmark.
Research
> What should I investigate about BPT?
Then:
> Why?
> What about managers?
Manager analysis
> Which BPT manager underperformed its benchmark most in Q4?
Then:
> Has that worsened?
Allocation
> Where was BPT furthest from policy in Q4?
Then:
> Show the quarterly trend.
Evidence
After a grounded answer:
> Source?
Final note
Beacon is a prototype rather than a production investment platform.
The main thing I wanted to demonstrate was how I would approach building an AI-enabled analytical product from the business problem backwards.
The final structure is deliberately separated into clear responsibilities:
```text
AI
Understand + orchestrate

        ↓

Python / DuckDB
Calculate + validate

        ↓

Research layer
Interpret + contextualise

        ↓

Structured UI
Deliver + interrogate
```
There are areas I would continue to improve, particularly snapshot-based local development, caching, model evaluation and conversational testing, but the architecture means those improvements can be made without rebuilding the entire product.
