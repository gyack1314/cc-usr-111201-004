"""读取活动节点和依赖引用，提供原始时长清单。"""
from .io import finite,unique
def summarize(value):
    unique(value['requests'],'request_id');result=[]
    for req in value['requests']:
        ids=unique(req['nodes'],'id')
        if req['source'] not in ids or req['sink'] not in ids:raise ValueError('起止节点缺失')
        for edge in req['edges']:
            if edge['from'] not in ids or edge['to'] not in ids or finite(edge['lag'])<0:raise ValueError('依赖引用错误')
        durations=[finite(x['duration']) for x in req['nodes']]
        if any(type(x) is not int or x<0 for x in durations):raise ValueError('节点时长错误')
        result.append({'request_id':req['request_id'],'nodes':len(ids),'edges':len(req['edges']),'sum_node_duration':sum(durations),'observed_wall':finite(req['wall_duration'])})
    return result
