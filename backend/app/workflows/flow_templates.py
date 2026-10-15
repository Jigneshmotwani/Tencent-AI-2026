"""Generic iteration 4 templates: Incident Assembler, Partner Change Radar, Vulnerability Remediation Tracker and Contractor
Offboarding Sweep. Every project, channel, folder, space and group is a template setting (demo pack `template_settings`
or Studio step settings); nothing demo-specific is hard-coded. Studio defaults come from the active scopes.
"""
from __future__ import annotations

from ..scopes import allowed, slack_write_channels
from .templates import SOURCES, WRITES, _vertical, edge, node

BRIEF_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['summary', 'suspected_cause', 'runbook_steps', 'recent_deploys', 'likely_owners'],
                'properties': {'summary': {'type': 'string'}, 'suspected_cause': {'type': 'string'},
                               'runbook_steps': {'type': 'array', 'items': {'type': 'string'}},
                               'recent_deploys': {'type': 'array', 'items': {'type': 'string'}},
                               'likely_owners': {'type': 'array', 'items': {'type': 'string'}}}}
CHANGE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['partner', 'change', 'affected_services', 'search_phrase'],
                 'properties': {'partner': {'type': 'string'}, 'change': {'type': 'string'}, 'effective_date': {'type': 'string'},
                                'affected_services': {'type': 'array', 'items': {'type': 'string'}},
                                'search_phrase': {'type': 'string', 'description': 'Two or three plain words to find an existing ticket; no quotes'}}}
EXISTS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['ticket_exists'],
                 'properties': {'ticket_exists': {'enum': ['yes', 'no']}, 'existing_key': {'type': 'string'}, 'reason': {'type': 'string'}}}
TICKET_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['summary', 'description'],
                 'properties': {'summary': {'type': 'string', 'maxLength': 200}, 'description': {'type': 'string'}}}
PERSON_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['email'],
                 'properties': {'email': {'type': 'string'}, 'name': {'type': 'string'}, 'last_day': {'type': 'string'}}}
CHECKLIST_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['summary', 'checklist'],
                    'properties': {'summary': {'type': 'string'}, 'checklist': {'type': 'array', 'items': {'type': 'string'}}}}


def _first(values, fallback):
    return values[0] if values else fallback


def _defaults() -> dict:
    projects, channels, folders = allowed('jira'), allowed('slack'), allowed('drive')
    writes = slack_write_channels()
    p0 = _first(projects, 'PROJECT')
    return {'project': p0, 'projects': projects, 'jql': f'project = {p0}', 'alert_channel': _first(channels, 'alerts'),
            'deploy_channel': channels[1] if len(channels) > 1 else _first(channels, 'deploys'),
            'incident_channel': _first(writes, _first(channels, 'incidents')), 'folder_id': _first(folders, 'FOLDER_ID'),
            'partner_channels': channels[:2] or ['partner-updates']}


def _spec(name, description, icon, trigger, inputs, nodes, edges, tools, sources=None, budget=2.0, steps=25, tokens=60000):
    for e in edges:
        if e.get('sourceHandle'):
            e['id'] = f"e_{e['source']}_{e['target']}_{e['sourceHandle']}"
    return {'name': name, 'description': description, 'icon': icon, 'trigger': trigger, 'inputs': inputs, 'nodes': nodes, 'edges': edges,
            'policies': {'model_alias': '', 'max_steps': steps, 'max_tokens_per_run': tokens, 'budget_usd': budget,
                         'allowed_sources': sources or SOURCES, 'allowed_tools': tools, 'approval_required_for': WRITES},
            'audience': {'kind': 'only_me', 'groups': [], 'roles': [], 'users': []}}


# ------------------------------------------------------------------------------------------------ 1. Incident Assembler
def incident_assembler(jql: str | None = None, alert_channel: str | None = None, deploy_channel: str | None = None,
                       incident_channel: str | None = None, approver_group: str = '', window: str = '2h') -> dict:
    d = _defaults()
    jql, alert, deploy, incident = jql or d['jql'], alert_channel or d['alert_channel'], deploy_channel or d['deploy_channel'], incident_channel or d['incident_channel']
    summary = '{{nodes.get_issue.output.fields.summary}}'
    nodes = [
        node('trigger', 'trigger.jira_issue_created', 'New incident', {'jql': jql, 'input': 'issue_key'}),
        node('get_issue', 'tool.builtin', 'Get the incident', {'tool': 'jira.get_issue', 'params': {'issue_key': '{{inputs.issue_key}}'}}),
        node('past', 'knowledge.retrieve', 'Similar incidents & postmortems', {'query': f'postmortem incident {summary}', 'sources': ['confluence', 'jira'],
                                                                              'max_documents': 5}),
        node('runbook', 'knowledge.retrieve', 'Current runbook', {'query': f'runbook {summary}', 'sources': ['confluence'], 'max_documents': 3}),
        node('alerts', 'tool.builtin', 'Alert channel (last 2 h)', {'tool': 'slack.read_channel', 'params': {'channel': alert, 'since': window}}),
        node('deploys', 'tool.builtin', 'Deploy channel (last 2 h)', {'tool': 'slack.read_channel', 'params': {'channel': deploy, 'since': window}}),
        node('guard_in', 'guardrail.input', 'Mask PII', {'pii': ['nric', 'iban', 'card', 'phone', 'email'], 'injection': True}),
        node('brief', 'llm.generate', 'Incident brief', {'prompt': (
            'Write an incident brief for {{inputs.issue_key}} using ONLY the evidence. Cite [E#] after every factual sentence. '
            'suspected_cause: the most likely cause and why; runbook_steps: the current runbook steps that apply, in order; recent_deploys: '
            'deploys or changes in the window that could be related; likely_owners: teams or people named in the evidence as owners. '
            'If the evidence does not cover a field, say "Not in the evidence" instead of guessing.'), 'include_evidence': True,
            'output_schema': BRIEF_SCHEMA, 'max_tokens': 1500}),
        node('check', 'guardrail.output', 'Check citations', {'target': 'brief', 'grounding': True, 'schema': True, 'pii_leak': True, 'on_fail': 'strip'}),
        node('approval', 'human.approval', 'Approve posting the brief', {
            'title': 'Post the incident brief for {{inputs.issue_key}}', 'approver_roles': ['builder', 'admin'], 'approver_group': approver_group,
            'allow_requester': True, 'respond_within': '15 minutes', 'message': 'Check the brief before it goes to #' + incident + ' and the Jira issue.'}),
        node('post', 'tool.builtin', 'Post to the incident channel', {'tool': 'slack.post_message', 'params': {
            'channel': incident, 'text': '*Incident brief {{inputs.issue_key}}*\n{{nodes.check.output.result.summary}}\n'
                                         'Suspected cause: {{nodes.check.output.result.suspected_cause}}'}}),
        node('comment', 'tool.builtin', 'Comment on the incident', {'tool': 'jira.comment', 'params': {
            'issue_key': '{{inputs.issue_key}}', 'body': 'Incident brief (assembled by agent, approved):\n{{nodes.check.output.result.summary}}\n'
                                                         'Suspected cause: {{nodes.check.output.result.suspected_cause}}'}}),
        node('output', 'output', 'Brief', {'template': '## Incident brief: {{inputs.issue_key}}\n{{nodes.check.output.result.summary}}\n\n'
                                                       '**Suspected cause:** {{nodes.check.output.result.suspected_cause}}', 'include_nodes': ['check']}),
    ]
    order = ['trigger', 'get_issue', 'past', 'runbook', 'alerts', 'deploys', 'guard_in', 'brief', 'check', 'approval', 'post', 'comment', 'output']
    edges = [edge(a, b) for a, b in zip(order, order[1:])]
    if not approver_group:
        nodes[9]['config'].pop('approver_group')
    _vertical(nodes, order, [], [])
    return _spec('Incident Assembler', 'Assembles a cited incident brief from past incidents, the runbook and recent alert and deploy chatter, then posts it after approval.',
                 'siren', {'type': 'jira_issue_created', 'config': {'jql': jql, 'input': 'issue_key'}},
                 [{'name': 'issue_key', 'type': 'string', 'required': True, 'description': 'Incident issue key, e.g. INC-12'}], nodes, edges,
                 ['builtin:jira.get_issue', 'builtin:slack.read_channel', 'builtin:slack.post_message', 'builtin:jira.comment'])


# ------------------------------------------------------------------------------------------------ 2. Partner Change Radar
def partner_change_radar(folder_id: str | None = None, partner_channels: list | None = None, ticket_project: str | None = None,
                         approver_group: str = '', window: str = '7d') -> dict:
    d = _defaults()
    folder, channels, project = folder_id or d['folder_id'], list(partner_channels or d['partner_channels'])[:3], ticket_project or d['project']
    nodes = [node('trigger', 'trigger.drive_file_added', 'New partner file', {'folder_id': folder, 'input': 'file_id'}),
             node('read_file', 'tool.builtin', 'Read the new file', {'tool': 'drive.get_file', 'params': {'file_id': '{{inputs.file_id}}'}})]
    chan_ids = []
    for i, ch in enumerate(channels):
        nid = f'slack_{i + 1}'
        chan_ids.append(nid)
        nodes.append(node(nid, 'tool.builtin', f'#{ch} announcements', {'tool': 'slack.read_channel', 'params': {'channel': ch, 'since': window}}))
    nodes += [
        node('catalog', 'knowledge.retrieve', 'Service catalog', {'query': 'service catalog owner {{nodes.read_file.output.name}}', 'sources': ['confluence'],
                                                                 'max_documents': 4}),
        node('analyze', 'llm.generate', 'Map the change to services', {'prompt': (
            'From the partner file and announcements in the evidence, identify the partner, the change and its effective date, and map it to the '
            'affected services using the service catalog. Cite [E#] in change. search_phrase: two or three plain words (no quotes) to find an '
            'existing Jira ticket about this change.'), 'include_evidence': True, 'output_schema': CHANGE_SCHEMA, 'max_tokens': 1500}),
        node('find_ticket', 'tool.builtin', 'Existing ticket?', {'tool': 'jira.search', 'params': {
            'jql': f'project = {project} AND text ~ "{{{{nodes.analyze.output.search_phrase}}}}"', 'max_results': 5}}),
        node('decide', 'llm.generate', 'Decide: ticket exists?', {'prompt': (
            'Does one of the Jira issues in the evidence already track this partner change: {{nodes.analyze.output.change}}? '
            'Answer ticket_exists "yes" with existing_key only if an issue clearly covers it; otherwise "no".'), 'include_evidence': True,
            'output_schema': EXISTS_SCHEMA, 'max_tokens': 300}),
        node('router', 'logic.router', 'Ticket exists?', {'value': '{{nodes.decide.output.ticket_exists}}', 'cases': ['yes', 'no'], 'default': 'no'}),
        node('draft', 'llm.generate', 'Draft the ticket', {'prompt': (
            'Draft a Jira ticket for the partner change {{nodes.analyze.output.change}} (partner {{nodes.analyze.output.partner}}). Description: '
            'what changes and when, affected services and their owners from the service catalog, and the next steps. Cite [E#].'),
            'include_evidence': True, 'output_schema': TICKET_SCHEMA, 'max_tokens': 1500}),
        node('approval', 'human.approval', 'Approve the ticket', {'title': 'Create a ticket for the partner change', 'approver_roles': ['builder', 'admin'],
                                                                  'approver_group': approver_group, 'allow_requester': True,
                                                                  'message': 'Check the drafted ticket before it is created in ' + project + '.'}),
        node('create', 'tool.builtin', 'Create the ticket', {'tool': 'jira.create_issue', 'params': {
            'project_key': project, 'issue_type': 'Task', 'labels': ['partner-change'], 'summary': '{{nodes.draft.output.summary}}',
            'description': '{{nodes.draft.output.description}}'}}),
        node('output', 'output', 'Result', {'template': '## Partner change\n{{nodes.analyze.output.change}}\n\nAffected services: '
                                                        '{{nodes.analyze.output.affected_services}}\n\nExisting ticket: {{nodes.decide.output.ticket_exists}} '
                                                        '{{nodes.decide.output.existing_key}}', 'include_nodes': ['analyze', 'decide']}),
    ]
    if not approver_group:
        next(n for n in nodes if n['id'] == 'approval')['config'].pop('approver_group')
    main = ['trigger', 'read_file', *chan_ids, 'catalog', 'analyze', 'find_ticket', 'decide', 'router', 'output']
    edges = [edge(a, b) for a, b in zip(main[:-2], main[1:-1])]
    edges += [edge('router', 'output', 'yes'), edge('router', 'draft', 'no'), edge('draft', 'approval'), edge('approval', 'create'), edge('create', 'output')]
    _vertical(nodes, main[:-1], [], ['draft', 'approval', 'create'])
    next(n for n in nodes if n['id'] == 'output')['position'] = {'x': 0, 'y': 140 * len(main)}
    return _spec('Partner Change Radar', 'Watches the partner folder, maps a partner change to affected services and drafts a ticket for approval if none exists.',
                 'search', {'type': 'drive_file_added', 'config': {'folder_id': folder, 'input': 'file_id'}},
                 [{'name': 'file_id', 'type': 'string', 'required': True, 'description': 'Drive file id of the new partner document'}], nodes, edges,
                 ['builtin:drive.get_file', 'builtin:slack.read_channel', 'builtin:jira.search', 'builtin:jira.create_issue'])


# ------------------------------------------------------------------------------------------------ 3. Vulnerability Remediation Tracker
def vulnerability_remediation_tracker(findings_project: str | None = None, team_projects: list | None = None, cron: str = '0 9 * * 1',
                                      timezone: str = '') -> dict:
    d = _defaults()
    findings = findings_project or d['project']
    teams = list(team_projects or [p for p in d['projects'] if p != findings][:3] or [findings])
    nodes = [
        node('trigger', 'trigger.schedule', 'Weekly', {'cron': cron, **({'timezone': timezone} if timezone else {})}),
        node('findings', 'tool.builtin', 'Open security findings', {'tool': 'jira.search', 'params': {
            'jql': f'project = {findings} AND statusCategory != Done ORDER BY priority DESC', 'max_results': 5}}),
        node('upgrades', 'tool.builtin', 'Team upgrade tickets', {'tool': 'jira.search', 'params': {
            'jql': 'project in (' + ', '.join(teams) + ') AND statusCategory != Done ORDER BY duedate ASC', 'max_results': 5}}),
        node('summary', 'llm.generate', 'Summary for you', {'prompt': (
            'Write this week\'s remediation summary for the reader using ONLY the Jira issues in the evidence, which are exactly what this reader '
            'can see. Cite [E#] after every factual sentence. Group by what needs action first (due soonest, highest priority). '
            'Never mention findings, counts, reports or projects that are not in the evidence, and never say that other items may exist. '
            'If the evidence is empty, say only: "Nothing needs your attention this week."'), 'include_evidence': True, 'max_tokens': 1800}),
        node('check', 'guardrail.output', 'Check citations', {'target': 'summary', 'grounding': True, 'schema': False, 'pii_leak': True, 'on_fail': 'strip'}),
        node('output', 'output', 'Weekly summary', {'template': '## Remediation this week\n{{nodes.check.output.result.text}}', 'include_nodes': ['check']}),
    ]
    order = [n['id'] for n in nodes]
    _vertical(nodes, order, [], [])
    return _spec('Vulnerability Remediation Tracker', 'Weekly summary of security findings and upgrade tickets, scoped to what each subscriber can see.',
                 'shield-check', {'type': 'schedule', 'config': {'cron': cron, **({'timezone': timezone} if timezone else {})}}, [], nodes,
                 [edge(a, b) for a, b in zip(order, order[1:])], ['builtin:jira.search'], sources=['jira'], budget=1.0, steps=10, tokens=30000)


# ------------------------------------------------------------------------------------------------ 4. Contractor Offboarding Sweep
def contractor_offboarding(jql: str | None = None, it_project: str | None = None, approver_group: str = '', days: int = 30) -> dict:
    d = _defaults()
    jql, project = jql or d['jql'], it_project or d['project']
    nodes = [
        node('trigger', 'trigger.jira_issue_created', 'Offboarding ticket', {'jql': jql, 'input': 'issue_key'}),
        node('get_ticket', 'tool.builtin', 'Read the ticket', {'tool': 'jira.get_issue', 'params': {'issue_key': '{{inputs.issue_key}}'}}),
        node('person', 'llm.generate', 'Find the contractor', {'prompt': (
            'From the offboarding ticket in the evidence, extract the contractor\'s work email (and name and last day if stated). '
            'If no email is written in the ticket, return an empty email.'), 'include_evidence': True, 'output_schema': PERSON_SCHEMA, 'max_tokens': 200}),
        node('inventory', 'tool.builtin', 'Access inventory', {'tool': 'access.inventory', 'params': {'user_email': '{{nodes.person.output.email}}'}}),
        node('activity', 'tool.builtin', 'App activity', {'tool': 'audit.user_activity', 'params': {'user_email': '{{nodes.person.output.email}}', 'days': days}}),
        node('checklist', 'llm.generate', 'Revocation checklist', {'prompt': (
            'Write a revocation checklist for {{nodes.person.output.email}} using ONLY the access inventory and activity evidence. One item per '
            'access to remove, citing [E#]. For every source marked NOT CHECKED, add an item asking IT to check it manually. Never claim a '
            'source was checked if the evidence says it was not.'), 'include_evidence': True, 'output_schema': CHECKLIST_SCHEMA, 'max_tokens': 1500}),
        node('check', 'guardrail.output', 'Check citations', {'target': 'checklist', 'grounding': True, 'schema': True, 'pii_leak': False, 'on_fail': 'strip'}),
        node('approval', 'human.approval', 'Approve the IT checklist', {'title': 'Create the offboarding checklist for {{inputs.issue_key}}',
                                                                        'approver_roles': ['admin'], 'approver_group': approver_group, 'allow_requester': True,
                                                                        'message': 'Check the checklist before the IT ticket and comment are created.'}),
        node('it_ticket', 'tool.builtin', 'Create the IT checklist ticket', {'tool': 'jira.create_issue', 'params': {
            'project_key': project, 'issue_type': 'Task', 'labels': ['offboarding'], 'summary': 'Offboarding checklist: {{nodes.person.output.email}}',
            'description': '{{nodes.check.output.result.summary}}\n\n{{nodes.check.output.result.checklist}}\n\nSource ticket: {{inputs.issue_key}}'}}),
        node('comment', 'tool.builtin', 'Comment on the ticket', {'tool': 'jira.comment', 'params': {
            'issue_key': '{{inputs.issue_key}}', 'body': 'Access inventory done; revocation checklist created by the offboarding agent (approved).'}}),
        node('output', 'output', 'Checklist', {'template': '## Offboarding: {{nodes.person.output.email}}\n{{nodes.check.output.result.summary}}',
                                               'include_nodes': ['check']}),
    ]
    if not approver_group:
        next(n for n in nodes if n['id'] == 'approval')['config'].pop('approver_group')
    order = [n['id'] for n in nodes]
    _vertical(nodes, order, [], [])
    return _spec('Contractor Offboarding Sweep', "When an offboarding ticket lands, inventories the contractor's access and activity and drafts a revocation checklist for approval.",
                 'clipboard-check', {'type': 'jira_issue_created', 'config': {'jql': jql, 'input': 'issue_key'}},
                 [{'name': 'issue_key', 'type': 'string', 'required': True, 'description': 'Offboarding ticket key'}], nodes,
                 [edge(a, b) for a, b in zip(order, order[1:])],
                 ['builtin:jira.get_issue', 'builtin:access.inventory', 'builtin:audit.user_activity', 'builtin:jira.create_issue', 'builtin:jira.comment'])


BUILDERS = {'incident_assembler': incident_assembler, 'partner_change_radar': partner_change_radar,
            'vulnerability_remediation_tracker': vulnerability_remediation_tracker, 'contractor_offboarding': contractor_offboarding}
