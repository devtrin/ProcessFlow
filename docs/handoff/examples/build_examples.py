from pathlib import Path
import json, copy
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
ROOT=Path(__file__).parent
schema={
 '$schema':'https://json-schema.org/draft/2020-12/schema', '$id':'https://example.local/processflow/process-graph.schema.json',
 'title':'ProcessFlow illustrative canonical ProcessGraph schema v0.1',
 'description':'Proposed data contract; Claude may refine with backwards-compatible explanation. This is not BPMN XML.',
 'type':'object','additionalProperties':False,
 'required':['schema_version','process_id','title','source','nodes','edges','issues'],
 'properties':{
  'schema_version':{'const':'0.1'}, 'process_id':{'type':'string','minLength':1},'title':{'type':'string'},
  'source':{'type':'object','additionalProperties':False,'required':['kind','filename','sha256'], 'properties':{'kind':{'enum':['pdf','bpmn_xml']},'filename':{'type':'string'},'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'},'imported_at':{'type':'string'},'original_id':{'type':['string','null']}}},
  'participants':{'type':'array','items':{'type':'object','required':['id','name'],'properties':{'id':{'type':'string'},'name':{'type':'string'},'source_id':{'type':['string','null']}}}},
  'lanes':{'type':'array','items':{'type':'object','required':['id','name'],'properties':{'id':{'type':'string'},'name':{'type':'string'},'participant_id':{'type':['string','null']},'node_ids':{'type':'array','items':{'type':'string'}}}}},
  'nodes':{'type':'array','items':{'$ref':'#/$defs/node'}},'edges':{'type':'array','items':{'$ref':'#/$defs/edge'}},
  'issues':{'type':'array','items':{'type':'object','required':['code','severity','message'],'properties':{'code':{'type':'string'},'severity':{'enum':['error','warning','info']},'message':{'type':'string'},'element_ids':{'type':'array','items':{'type':'string'}},'resolved':{'type':'boolean'}}}},
  'review_state':{'enum':['draft','needs_review','reviewed']}
 },
 '$defs':{
  'sourceRef':{'type':'object','required':['type'],'properties':{'type':{'enum':['pdf','bpmn_xml']},'page':{'type':'integer','minimum':1},'quote':{'type':'string'},'bbox':{'type':'array','items':{'type':'number'},'minItems':4,'maxItems':4},'element_id':{'type':'string'},'xpath':{'type':'string'}}},
  'dataItem':{'type':'object','additionalProperties':False,'required':['id','name','direction','state','origin','source_refs'],'properties':{'id':{'type':'string'},'name':{'type':'string'},'description':{'type':'string'},'direction':{'enum':['input','output']},'state':{'enum':['included','excluded']},'origin':{'enum':['source_explicit','ai_inferred','user_added']},'source_refs':{'type':'array','items':{'$ref':'#/$defs/sourceRef'}},'edited_from_id':{'type':['string','null']}}},
  'node':{'type':'object','additionalProperties':False,'required':['id','kind','label','inputs','outputs','source_refs','review_state'], 'properties':{'id':{'type':'string'},'source_id':{'type':['string','null']},'kind':{'enum':['start_event','end_event','intermediate_event','task','subprocess','exclusive_gateway','parallel_gateway','inclusive_gateway','event_gateway','unknown']},'label':{'type':'string'},'participant_id':{'type':['string','null']},'lane_id':{'type':['string','null']},'inputs':{'type':'array','items':{'$ref':'#/$defs/dataItem'}},'outputs':{'type':'array','items':{'$ref':'#/$defs/dataItem'}},'source_refs':{'type':'array','items':{'$ref':'#/$defs/sourceRef'}},'review_state':{'enum':['unverified','needs_review','verified','source_explicit']},'layout':{'type':'object','properties':{'x':{'type':'number'},'y':{'type':'number'}}},'attributes':{'type':'object'}}},
  'edge':{'type':'object','additionalProperties':False,'required':['id','source','target','kind','source_refs','review_state'],'properties':{'id':{'type':'string'},'source':{'type':'string'},'target':{'type':'string'},'kind':{'enum':['sequence','message','association','inferred_sequence']},'label':{'type':['string','null']},'condition_expression':{'type':['string','null']},'is_default':{'type':'boolean'},'source_refs':{'type':'array','items':{'$ref':'#/$defs/sourceRef'}},'review_state':{'enum':['unverified','needs_review','verified','source_explicit']}}}
 }
}
(ROOT/'process-graph.schema.json').write_text(json.dumps(schema,indent=2,ensure_ascii=False)+'\n')

ref=lambda el:{'type':'bpmn_xml','element_id':el,'xpath':f"//*[@id='{el}']"}
item=lambda id,name,direction:{'id':id,'name':name,'direction':direction,'state':'included','origin':'source_explicit','source_refs':[ref('Task_Review')]}
nodes=[]
for id,kind,label,inputs,outputs in [
 ('Start_1','start_event','Request received',[],[]),
 ('Task_Review','task','Review request',[item('in1','Purchase request','input'),item('in2','Budget reference','input')],[item('out1','Review result','output')]),
 ('Gateway_Approved','exclusive_gateway','Approved?',[],[]),
 ('Task_Approve','task','Record approval',[],[item('out2','Approved request','output')]),
 ('Task_Reject','task','Notify requester',[],[item('out3','Rejection notice','output')]),
 ('End_1','end_event','Process complete',[],[])]:
 nodes.append({'id':id,'source_id':id,'kind':kind,'label':label,'participant_id':None,'lane_id':'Lane_Manager' if id in ['Task_Review','Gateway_Approved','Task_Approve','Task_Reject'] else 'Lane_Requester','inputs':inputs,'outputs':outputs,'source_refs':[ref(id)],'review_state':'source_explicit'})
flows=[('F1','Start_1','Task_Review',None),('F2','Task_Review','Gateway_Approved',None),('F3','Gateway_Approved','Task_Approve','Yes'),('F4','Gateway_Approved','Task_Reject','No'),('F5','Task_Approve','End_1',None),('F6','Task_Reject','End_1',None)]
edges=[{'id':fid,'source':s,'target':t,'kind':'sequence','label':label,'source_refs':[ref(fid)],'review_state':'source_explicit'} for fid,s,t,label in flows]
example={'schema_version':'0.1','process_id':'Process_Purchase','title':'Purchase request handling (fictional example)','source':{'kind':'bpmn_xml','filename':'example-no-di.bpmn','sha256':'0'*64,'original_id':'Process_Purchase'},'participants':[{'id':'Participant_Main','name':'Operations'}],'lanes':[{'id':'Lane_Requester','name':'Requester','node_ids':['Start_1','End_1']},{'id':'Lane_Manager','name':'Manager','node_ids':['Task_Review','Gateway_Approved','Task_Approve','Task_Reject']}], 'nodes':nodes,'edges':edges,'issues':[],'review_state':'reviewed'}
(ROOT/'sample-process.json').write_text(json.dumps(example,indent=2,ensure_ascii=False)+'\n')
variant={'schema_version':'0.1','variant_id':'variant-001','name':'Pilot rollout','baseline_process_id':'Process_Purchase','baseline_source_sha256':'0'*64,'version':1,'changes':[{'op':'exclude','node_id':'Task_Review','item_id':'in2','direction':'input','reason':'Budget reference not used in pilot'}, {'op':'add','node_id':'Task_Review','direction':'output','item':{'id':'out_user_1','name':'Review checklist','direction':'output','state':'included','origin':'user_added','source_refs':[]}}], 'note':'Fictional example; baseline source sha256 is placeholder.'}
(ROOT/'sample-variant.json').write_text(json.dumps(variant,indent=2)+'\n')

xml_header='''<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
             xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
             xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
             id="Definitions_1" targetNamespace="http://example.org/processflow/examples">
  <collaboration id="Collaboration_1"><participant id="Participant_Main" name="Operations" processRef="Process_Purchase"/></collaboration>
  <process id="Process_Purchase" name="Purchase request handling" isExecutable="false">
    <laneSet id="LaneSet_1">
      <lane id="Lane_Requester" name="Requester"><flowNodeRef>Start_1</flowNodeRef><flowNodeRef>End_1</flowNodeRef></lane>
      <lane id="Lane_Manager" name="Manager"><flowNodeRef>Task_Review</flowNodeRef><flowNodeRef>Gateway_Approved</flowNodeRef><flowNodeRef>Task_Approve</flowNodeRef><flowNodeRef>Task_Reject</flowNodeRef></lane>
    </laneSet>
    <startEvent id="Start_1" name="Request received"/>
    <task id="Task_Review" name="Review request"/>
    <exclusiveGateway id="Gateway_Approved" name="Approved?" default="F4"/>
    <task id="Task_Approve" name="Record approval"/>
    <task id="Task_Reject" name="Notify requester"/>
    <endEvent id="End_1" name="Process complete"/>
    <sequenceFlow id="F1" sourceRef="Start_1" targetRef="Task_Review"/>
    <sequenceFlow id="F2" sourceRef="Task_Review" targetRef="Gateway_Approved"/>
    <sequenceFlow id="F3" name="Yes" sourceRef="Gateway_Approved" targetRef="Task_Approve"/>
    <sequenceFlow id="F4" name="No" sourceRef="Gateway_Approved" targetRef="Task_Reject"/>
    <sequenceFlow id="F5" sourceRef="Task_Approve" targetRef="End_1"/>
    <sequenceFlow id="F6" sourceRef="Task_Reject" targetRef="End_1"/>
  </process>
'''
(ROOT/'example-no-di.bpmn').write_text(xml_header+'</definitions>\n')
di='''  <bpmndi:BPMNDiagram id="Diagram_1">
    <bpmndi:BPMNPlane id="Plane_1" bpmnElement="Collaboration_1">
'''
positions={'Start_1':(120,185,36,36),'Task_Review':(215,155,130,78),'Gateway_Approved':(395,169,52,52),'Task_Approve':(530,75,135,78),'Task_Reject':(530,252,135,78),'End_1':(730,185,36,36)}
for id,(x,y,w,h) in positions.items():
 di+=f'      <bpmndi:BPMNShape id="DI_{id}" bpmnElement="{id}"><dc:Bounds x="{x}" y="{y}" width="{w}" height="{h}"/></bpmndi:BPMNShape>\n'
for fid,s,t,label in flows:
 x,y,w,h=positions[s]; xt,yt,wt,ht=positions[t]
 di+=f'      <bpmndi:BPMNEdge id="DI_{fid}" bpmnElement="{fid}"><di:waypoint x="{x+w}" y="{y+h/2}"/><di:waypoint x="{xt}" y="{yt+ht/2}"/></bpmndi:BPMNEdge>\n'
di+='    </bpmndi:BPMNPlane>\n  </bpmndi:BPMNDiagram>\n'
(ROOT/'example-with-di.bpmn').write_text(xml_header+di+'</definitions>\n')
(ROOT/'invalid-xxe.xml').write_text('''<?xml version="1.0"?>
<!DOCTYPE definitions [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d" targetNamespace="http://example.org">&xxe;</definitions>
''')

narrative='''PROCESS: PURCHASE REQUEST HANDLING (FICTIONAL TEST FIXTURE)
Owner: Operations. Version: 1.0. This is invented for software testing.

1. The requester submits a purchase request and its budget reference to the manager.
2. The manager reviews the purchase request and checks the budget reference.
   INPUTS: purchase request; budget reference. OUTPUT: review result.
3. DECISION: Is the request approved?
   YES: The manager records the approval. OUTPUT: approved request.
   NO: The manager notifies the requester. OUTPUT: rejection notice.
4. Both branches terminate when the process completes.

AMBIGUITY TO TEST: In some cases, the request may need another review.
The text does not say who initiates it or when. Do not invent this step.
'''
(ROOT/'example-process-narrative.txt').write_text(narrative)
pdf=canvas.Canvas(str(ROOT/'example-process-narrative.pdf'),pagesize=A4)
pdf.setTitle('Fictional process narrative fixture - generated')
x,y=60,780
for n,line in enumerate(narrative.splitlines()):
 if n==0: pdf.setFont('Helvetica-Bold',13)
 else: pdf.setFont('Helvetica',10)
 if line.startswith('AMBIGUITY'): pdf.setFillColorRGB(.55,.23,.12)
 else: pdf.setFillColorRGB(.12,.17,.25)
 pdf.drawString(x,y,line[:115]);y-=27 if line=='' else 19
pdf.save()
print('generated examples')
