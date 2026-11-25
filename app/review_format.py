"""Markdown-safe, action-first review rendering."""
import re
from urllib.parse import quote


def cell(value):
    from app.github_auth import strip_emojis
    value = strip_emojis(str(value))
    return ' '.join(str(value).split()).replace('|', '&#124;')


def details(title, body):
    return f'<details>\n<summary>{title}</summary>\n\n{body}\n\n</details>'


def diagram(value):
    value = (value or '').strip()
    if not value:
        return 'Diagram unavailable: no architecture diagram supplied.'
    if value.startswith('```'):
        match = re.fullmatch(r'```mermaid\s*\n(.*?)\n```', value, re.S)
        if not match:
            return 'Diagram unavailable: malformed Mermaid fence.'
        value = match.group(1)
    if '```' in value or not re.match(r'^(flowchart\s+(?:LR|RL|TB|TD|BT)\b|graph\s+(?:LR|RL|TB|TD|BT)\b|sequenceDiagram\b)', value):
        return 'Diagram unavailable: unsupported diagram declaration.'
    return f'```mermaid\n{value}\n```'


def render_review(data):
    seen = set()
    findings = []
    for item in data.get('findings') or []:
        source = item.get('source') or ('scanner' if str(item.get('title','')).startswith('Secret Leak Detected:') else 'llm')
        key = (item.get('file'), item.get('line'), source, ' '.join(str(item.get('title','')).lower().split()))
        if key in seen:
            continue
        seen.add(key)
        findings.append(dict(item, source=source))
    findings.sort(key=lambda f: (0 if f['source']=='scanner' else {'bug':1,'risk':2,'nit':3}.get(f.get('severity'),2)))
    counts = {kind: sum(f.get('severity') == kind for f in findings) for kind in ('bug','risk','nit')}
    if data.get('findings') is None:
        counts = dict(bug=data['bug_count'],risk=data['risk_count'],nit=data['nit_count'])
    verdict = data['verdict']
    secrets = sum(f['source']=='scanner' for f in findings) if data.get('findings') is not None else data.get('secret_alerts_count',0)
    if any(f['source']=='scanner' and f.get('confidence') != 'POTENTIAL' for f in findings) or (data.get('findings') is None and secrets):
        verdict = 'BLOCK'
    elif findings and all(f.get('confidence')=='POTENTIAL' for f in findings):
        verdict = 'WARN'
    elif findings and verdict == 'PASS':
        verdict = 'WARN'
    summary = ' '.join(re.split(r'(?<=[.!?])\s+', data['summary'].strip())[:3])
    parts = ['<!-- patchpilot-ai-review -->', '## PatchPilot AI PR Review',
             f"**Requested by:** @{data.get('requested_by') or 'maintainer'} · **Verdict:** `{verdict}` · **Commit:** `{data['commit_sha'][:7]}`",
             f"**Verification:** `{data.get('verification_status') or 'Not reported'}`. Scanner detections are separate from LLM verification.",
             f"**Findings:** {counts['bug']} bugs · {counts['risk']} risks · {counts['nit']} nits · {secrets} scanner alerts", summary]
    if data.get('triage_summary'):
        parts.append(details('Triage & Impact',data['triage_summary']))
    if data.get('fallback_used'):
        fb=data['fallback_used']
        parts.append(f"**Failover Alert**: Primary LLM returned *{fb.get('primary_reason','Unavailable')}*. Review successfully completed via fallback: **{str(fb.get('provider','')).capitalize()}** (`{fb.get('model','')}`)")
    blocks=[]; nits=[]
    for index,f in enumerate(findings,1):
        location=f"`{f.get('file','unknown')}`:{f.get('line','?')}"
        if data.get('repo_full_name'):
            url=f"https://github.com/{data['repo_full_name']}/blob/{data['commit_sha']}/{quote(str(f.get('file','')),safe='/')}#L{f.get('line',1)}"
            location=f"[{location}]({url})"
        body=f"#### {index}. [{f.get('severity','risk').upper()}] {f.get('title','Finding')}\n\n{location} · Source: {f['source']}\n\n{f.get('explanation') or f.get('comment') or 'No explanation supplied.'}"
        if f.get('suggestion'):
            fence='`'*max(3,1+max((len(x) for x in re.findall(r'`+',f['suggestion'])),default=0))
            body+=f"\n\nSuggested change (apply through the inline review):\n\n{fence}\n{f['suggestion']}\n{fence}"
        if f.get('agent_prompt'):
            body+='\n\n'+details('Suggested agent prompt',f['agent_prompt'])
        (nits if f.get('severity')=='nit' else blocks).append(body)
    parts+=['### Actionable findings', '\n\n'.join(blocks) or 'No actionable findings were reported within the reviewed scope.']
    if nits: parts.append(details('Nits', '\n\n'.join(nits)))
    parts.append(details('Architecture diagram',diagram(data.get('architecture_diagram'))))
    if data.get('walkthrough'):
        rows=['| File | Impact | Summary |','| :--- | :--- | :--- |']
        rows += ['| '+ ' | '.join(cell(f.get(k,'')) for k in ('file','impact','summary'))+' |' for f in data['walkthrough']]
        parts.append(details('File walkthrough','\n'.join(rows)))
    if data.get('suggested_tests'):
        parts.append(details('Suggested tests (not executed)', '\n\n'.join(f"**{cell(t.get('target_file',''))}**: {t.get('description','')}\n\n```python\n{t.get('code_snippet','')}\n```" for t in data['suggested_tests'])))
    return '\n\n'.join(p for p in parts if p)+'\n'


def finalize_review(review):
    findings=[]
    seen=set()
    for f in review.get('findings',[]):
        source=f.get('source') or ('scanner' if str(f.get('title','')).startswith('Secret Leak Detected:') else 'llm')
        key=(f.get('file'),f.get('line'),source,' '.join(str(f.get('title','')).lower().split()))
        if key in seen: continue
        seen.add(key)
        f=dict(f,source=source)
        if f.get('confidence')=='POTENTIAL':
            f['severity']='risk'
            f['title']='Potential private-key pattern'
            f['explanation']='A private-key header was detected. A header alone does not establish that usable private-key material was exposed. Inspect this location; the content was redacted before analysis.'
        findings.append(f)
    review['findings']=findings
    scanner=[f for f in findings if f['source']=='scanner']
    if any(f.get('confidence')!='POTENTIAL' for f in scanner): review['verdict']='BLOCK'
    elif findings and (review.get('verdict')=='PASS' or all(f.get('confidence')=='POTENTIAL' for f in findings)): review['verdict']='WARN'
    review['review_checks']={'Security':f'{len(scanner)} scanner detections', 'Correctness':f"{sum(f.get('severity')=='bug' for f in findings)} reported bugs", 'Performance':'No performance guarantee; inspect findings', 'Test Coverage':'Coverage not measured'}
    return review
