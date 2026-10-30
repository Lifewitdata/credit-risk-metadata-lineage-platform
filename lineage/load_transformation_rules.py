"""Validate an authoritative dataset rule snapshot, then reconcile it atomically.
Missing rules/edges are retired, never erased. Revisions retain before/after evidence.
"""
import json
from pathlib import Path
import networkx as nx
import pandas as pd
from sqlalchemy import text

RULES_CSV = Path(__file__).resolve().parents[1] / 'data/raw/transformation_rules.csv'
TARGET_DATASET_NAME = 'credit_datamart_sme_loans'
RULE_COLUMNS = ['target_field','transformation_type','transformation_logic',
                'business_justification','created_by','verification_status','evidence_ref','verified_at','verified_by']


def validate_rules(frame):
    required = {'rule_name','target_dataset','source_kind','source_ref','source_dataset','source_field',*RULE_COLUMNS}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError('Rule snapshot is empty or missing required columns; refusing to retire everything.')
    frame = frame.fillna('').copy()
    for col in frame:
        frame[col] = frame[col].astype(str).str.strip()
    if set(frame.target_dataset) != {TARGET_DATASET_NAME}:
        raise ValueError('Snapshot must contain only the governed dataset.')
    graph = nx.DiGraph()
    for name, group in frame.groupby('rule_name'):
        if not name or any(group[c].nunique(dropna=False) != 1 for c in RULE_COLUMNS):
            raise ValueError(f'Conflicting or blank canonical definition for {name}')
        row = group.iloc[0]
        if row.verification_status not in {'draft','confirmed'}:
            raise ValueError('Unknown verification status')
        if row.verification_status == 'confirmed':
            if not all(row[c] for c in ['evidence_ref','verified_at','verified_by']):
                raise ValueError('Confirmed rules require evidence, reviewer and date')
            if (group.source_dataset == 'UNVERIFIED').any() or (group.source_field == 'UNVERIFIED').any():
                raise ValueError('Cannot confirm unknown source fields')
        if row.verified_at:
            from datetime import date
            date.fromisoformat(row.verified_at)
        if not row.target_field or not row.transformation_logic:
            raise ValueError('Blank target or logic')
        if row.transformation_type not in {'Direct Copy','Calculated','Aggregated','Mapped/Coded','Bureau Feed'}:
            raise ValueError('Unknown transformation type')
    if frame.groupby('target_field').rule_name.nunique().max() > 1:
        raise ValueError('One active canonical rule per target is required')
    for row in frame.itertuples():
        if row.source_kind not in {'Internal','External'} or not row.source_ref:
            raise ValueError('Invalid source kind/reference')
        if row.source_kind == 'Internal':
            if row.source_dataset != TARGET_DATASET_NAME or row.source_field != row.source_ref:
                raise ValueError('Internal source identity mismatch')
            graph.add_edge(row.source_ref, row.target_field)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError('Rule snapshot contains a cycle')
    return frame


def audit(conn, kind, key, action, old, new):
    conn.execute(text('''INSERT INTO lineage_change_log
        (entity_type,entity_id,action,old_json,new_json,changed_by)
        VALUES (:kind,:key,:action,:old,:new,:actor)'''),
        dict(kind=kind,key=key,action=action,
             old=json.dumps(old,default=str) if old is not None else None,
             new=json.dumps(new,default=str) if new is not None else None,actor='lineage_snapshot_loader'))


def upsert(conn, table, identity, values, kind):
    # Table/column names are supplied only by this module.
    pk = {'transformation_rules':'rule_id','lineage_nodes':'node_id','lineage_edges':'edge_id'}[table]
    where = ' AND '.join(f'{k}=:{k}' for k in identity)
    old = conn.execute(text(f'SELECT * FROM {table} WHERE {where}'),identity).mappings().first()
    values = {**values,'is_active':1}
    if old:
        changed = any((str(old[k]) if old[k] is not None else None) != (str(v) if v is not None else None) for k,v in values.items())
        if changed:
            conn.execute(text(f'UPDATE {table} SET '+','.join(f'{k}=:v_{k}' for k in values)+f' WHERE {pk}=:key'),
                         {**{f'v_{k}':v for k,v in values.items()},'key':old[pk]})
            audit(conn,kind,old[pk],'update',dict(old),{**dict(old),**values})
        return old[pk]
    data={**identity,**values}
    result=conn.execute(text(f'INSERT INTO {table} ('+','.join(data)+') VALUES ('+','.join(':'+k for k in data)+')'),data)
    key=result.lastrowid
    audit(conn,kind,key,'insert',None,data)
    return key


def load(engine, rules_path=RULES_CSV):
    frame=validate_rules(pd.read_csv(rules_path,keep_default_na=False))
    derived=set(frame.loc[(frame.source_kind=='Internal') | (frame.transformation_type=='Calculated'),'target_field'])
    with engine.begin() as conn:
        lock=' FOR UPDATE' if conn.dialect.name=='mysql' else ''
        ds=conn.execute(text('SELECT dataset_id FROM datasets WHERE dataset_name=:name'+lock),{'name':TARGET_DATASET_NAME}).scalar_one()
        desired_rules,desired_edges,desired_nodes=set(),set(),set()
        for name,group in frame.groupby('rule_name',sort=True):
            row=group.iloc[0]
            values={k:(row[k] or None) for k in RULE_COLUMNS}
            values['target_dataset_id']=ds
            rule=upsert(conn,'transformation_rules',{'rule_name':name},values,'rule')
            desired_rules.add(rule)
            target=upsert(conn,'lineage_nodes',{'dataset_id':ds,'field_name':row.target_field},
                          {'node_type':'Derived Field' if row.target_field in derived else 'Source Field'},'node')
            desired_nodes.add(target)
            for edge in group.itertuples():
                source_ds=ds
                field=edge.source_ref
                kind='Derived Field' if field in derived else 'Source Field'
                if edge.source_kind=='External':
                    source_name=edge.source_ref+' / '+edge.source_dataset
                    source_ds=conn.execute(text('SELECT dataset_id FROM datasets WHERE dataset_name=:name'),{'name':source_name}).scalar()
                    if source_ds is None:
                        source_ds=conn.execute(text('''INSERT INTO datasets(dataset_name,physical_table_name,description,business_owner)
                            VALUES (:name,'EXTERNAL_FEED',:description,'Unassigned upstream owner')'''),
                            {'name':source_name,'description':'Illustrative external source boundary; not a connected vendor feed.'}).lastrowid
                    field=edge.source_field if edge.source_field!='UNVERIFIED' else 'UNVERIFIED:'+edge.target_field
                    kind='External Feed'
                source=upsert(conn,'lineage_nodes',{'dataset_id':source_ds,'field_name':field},{'node_type':kind},'node')
                desired_nodes.add(source)
                link=upsert(conn,'lineage_edges',{'source_node_id':source,'target_node_id':target},{'rule_id':rule},'edge')
                desired_edges.add(link)
        # Retire only relationships/rules owned by this dataset.
        old_edges=conn.execute(text('''SELECT e.* FROM lineage_edges e JOIN transformation_rules r ON r.rule_id=e.rule_id
            WHERE r.target_dataset_id=:ds AND e.is_active=1'''),{'ds':ds}).mappings().all()
        old_rules=conn.execute(text('SELECT * FROM transformation_rules WHERE target_dataset_id=:ds AND is_active=1'),{'ds':ds}).mappings().all()
        for table,pk,kind,rows,wanted in [('lineage_edges','edge_id','edge',old_edges,desired_edges),('transformation_rules','rule_id','rule',old_rules,desired_rules)]:
            for old in rows:
                if old[pk] not in wanted:
                    conn.execute(text(f'UPDATE {table} SET is_active=0 WHERE {pk}=:key'),{'key':old[pk]})
                    audit(conn,kind,old[pk],'retire',dict(old),{**dict(old),'is_active':0})
        # Nodes disconnected by this snapshot remain stored for history, but leave current graph.
        for old in conn.execute(text('SELECT * FROM lineage_nodes WHERE is_active=1')).mappings().all():
            used=conn.execute(text('SELECT 1 FROM lineage_edges WHERE is_active=1 AND (source_node_id=:key OR target_node_id=:key)'),{'key':old['node_id']}).first()
            if not used and old['node_id'] not in desired_nodes:
                conn.execute(text('UPDATE lineage_nodes SET is_active=0 WHERE node_id=:key'),{'key':old['node_id']})
                audit(conn,'node',old['node_id'],'retire',dict(old),{**dict(old),'is_active':0})
        from lineage.build_graph import build_graph, validate_dag
        # Same transaction: a cycle rolls back every change.
        validate_dag(build_graph(conn))
    print(f'[lineage] reconciled {len(desired_rules)} active rules and {len(desired_edges)} edges')

if __name__=='__main__':
    from config.db_config import get_engine
    load(get_engine())
