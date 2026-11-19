from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], textColor=colors.HexColor("#1F3A5F"), fontSize=14, spaceBefore=10)
B = ParagraphStyle("B", parent=ss["BodyText"], fontSize=10, leading=14)
CELL = ParagraphStyle("c", parent=B, fontSize=9, leading=12)
NOTE = ParagraphStyle("N", parent=B, backColor=colors.HexColor("#FFF4D6"), borderPadding=6, spaceBefore=6, spaceAfter=8)
S = []
def p(t, st=B): S.append(Paragraph(t, st)); S.append(Spacer(1, 5))
def h(t): S.append(Paragraph(t, H1))
def c(t): return Paragraph(t, CELL)
def tbl(rows, widths):
    t = Table([[c(x) for x in r] for r in rows], colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#DCE8F7")), ("GRID", (0,0), (-1,-1), 0.4, colors.grey), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    S.append(t); S.append(Spacer(1, 8))

S.append(Paragraph("Cloud-Native Task Flow Platform", ParagraphStyle("T", parent=ss["Title"], textColor=colors.HexColor("#1F3A5F"))))
p("<b>AWS, Docker, PostgreSQL, REST APIs, CI/CD</b><br/>Author: Umesh Chandra Namani")
p("<b>About this report:</b> it explains the design and what the repository contains. Load-test numbers and the real AWS deployment "
  "are left for the author to run; no performance figures are invented here. The back end and its 52 tests were run on SQLite; the same tests run on "
  "PostgreSQL in the CI pipeline. The browser UI was syntax-checked but not clicked through while this repo was prepared.", NOTE)

h("1. The idea in simple words")
p("TaskFlow lets teams plan work together. People sign in, join projects, and move tasks across a board (To do, In progress, Review, Done). "
  "What each person may do depends on their role. Dashboards show how the project is going, and when anyone changes a task, everyone else "
  "watching the project sees it right away without refreshing. The back end runs in Docker containers on AWS and is updated automatically by a CI/CD pipeline.")
S.append(Image("docs/architecture.png", width=16.5*cm, height=16.5*cm*6.4/11.5)); S.append(Spacer(1, 6))

h("2. Who can do what (role-based access control)")
tbl([["Permission", "viewer", "member", "manager", "owner", "admin"],
     ["Read project, tasks, dashboard", "yes", "yes", "yes", "yes", "yes"],
     ["Create tasks", "", "yes", "yes", "yes", "yes"],
     ["Edit own / assigned tasks", "", "yes", "yes", "yes", "yes"],
     ["Edit any task, delete tasks", "", "", "yes", "yes", "yes"],
     ["Assign tasks to others", "", "self only", "yes", "yes", "yes"],
     ["Manage members, rename/delete project", "", "", "", "yes", "yes"],
     ["Manage users and global roles", "", "", "", "", "yes"]],
    [6.2*cm, 2.0*cm, 2.3*cm, 2.2*cm, 2.0*cm, 2.0*cm])
p("Every check happens on the server. People who are not in a project get a 404 so its existence is not revealed. "
  "Roles are read from the database on each request, so removing access works immediately.")

h("3. Paginated and cached REST APIs")
p("<b>Pagination and filters.</b> Task lists take <font face='Courier'>page</font>, <font face='Courier'>page_size</font> (maximum 100), status, priority, assignee, "
  "text search, overdue and sort options. Sort columns come from a fixed list and all values are sent to the database as parameters, so the API is safe from SQL injection. "
  "Database indexes match the common filters.")
p("<b>Caching, two layers.</b> (1) The server keeps recent answers in memory. Every project has a version number that is part of the cache key; any change bumps it, "
  "so stale answers are never served. (2) The same version forms an <font face='Courier'>ETag</font>. A browser that already has the latest data sends "
  "<font face='Courier'>If-None-Match</font> and receives a tiny <font face='Courier'>304</font> without any database query. Access is checked before the 304, so it cannot leak data.")

h("4. Live updates and dashboards")
S.append(Image("docs/realtime.png", width=16.5*cm, height=16.5*cm*5.2/11.5)); S.append(Spacer(1, 4))
p("Each change writes an <i>event</i> row in the same database transaction as the change itself, so an event exists only if the change was saved. "
  "Every app process polls for new events, tells its connected browsers using Server-Sent Events, and clears its own cache for that project. "
  "Because the database is the shared channel, several containers stay in step without a separate message broker. "
  "A browser that was offline can reconnect and replay the events it missed. Dashboards show status and priority breakdowns, overdue and unassigned tasks, "
  "workload per person, and tasks completed per day over the last week.")

h("5. Docker, CI/CD and AWS")
tbl([["Piece", "What it does"],
     ["Dockerfile / docker-compose", "Small non-root image; compose starts the app with PostgreSQL and demo data. A second app profile shows live updates across two servers."],
     ["GitHub Actions", "On each push: front-end syntax check, tests on SQLite and PostgreSQL, build the image and smoke-test it. On main: push to ECR and roll out to ECS, wait until stable."],
     ["Terraform (AWS)", "ECR, ECS Fargate service (2 tasks), Application Load Balancer with health checks, RDS PostgreSQL, Secrets Manager, CloudWatch logs, optional HTTPS and a keyless GitHub deploy role."],
     ["Safe releases", "Rolling deployment, health checks and ECS's circuit breaker, which rolls back a failing release by itself."]],
    [4.2*cm, 12.6*cm])

h("6. What was verified")
tbl([["Item", "Status"],
     ["REST API, RBAC, caching, events (52 automated tests)", "Run and passing on SQLite, including a live streaming test over a real HTTP server and a two-process cache test"],
     ["Same tests on PostgreSQL", "Set up in the CI workflow; not run in the preparation environment (no PostgreSQL available)"],
     ["Docker images, compose, Terraform, deployment workflow", "Written and syntax-checked; not run (no Docker or AWS access); expect small fixes on first run"],
     ["Browser UI", "JavaScript syntax-checked and served by the app; not clicked through in a browser"],
     ["Load test tool", "Run locally against a small test server to confirm that it works; no performance numbers are claimed"],
     ["'Hundreds of concurrent users' and release-time improvement", "Not measured here: use loadtest/loadtest.py and your own deployment history"]],
    [6.0*cm, 10.8*cm])

h("7. Load-test results (to fill in)")
tbl([["Users", "Duration", "Req/s", "p50 ms", "p95 ms", "p99 ms", "Errors", "Server size"],
     ["", "", "", "", "", "", "", ""], ["", "", "", "", "", "", "", ""]],
    [1.8*cm, 2.0*cm, 1.9*cm, 1.9*cm, 1.9*cm, 1.9*cm, 1.9*cm, 3.5*cm])
p("Run <font face='Courier'>python loadtest/loadtest.py --base URL --users 300 --duration 120 --sse 100</font> from another machine, increase users step by step, "
  "and write down the server size you tested.")

h("8. Limits and next steps")
p("Live connections use one server thread each, so very large audiences need a different channel (Redis or PostgreSQL LISTEN/NOTIFY). "
  "Offset pagination slows on very deep pages. Tokens sit in sessionStorage; httpOnly cookies with CSRF protection are stronger. "
  "Add database migrations (Alembic), HTTPS with an ACM certificate, comments, attachments and an audit log.")
SimpleDocTemplate("docs/TaskFlow_Report.pdf", pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
                  title="Cloud-Native Task Flow Platform - Report", author="Umesh Chandra Namani").build(S)
