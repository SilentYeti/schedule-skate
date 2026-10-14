"""Push up and down actual captured roads, without Unity or saves."""
from pathlib import Path
import argparse
import hashlib
import json
import math

from headless import PROJECT
from replay_runtime import ground_height, load_payload, riding_speed_loss
from riding_checks import check, engine, step


def run(args):
    roads=[('steep_grade','data/diagnostics/v0116-hills/SkateScheduleDiagnostics/collision-639275145115648613-0057.skb',2,-66.1,2.,-10.,math.pi),
           ('suburban_grade','data/diagnostics/v0117-suburban/SkateScheduleDiagnostics/collision-639275159382341722-0057.skb',0,-214.3,-178.,-190.,-math.pi/2),
           ('second_grade','data/diagnostics/v0117-suburban/SkateScheduleDiagnostics/collision-639275160098688865-0087.skb',2,60.7,72.,60.,math.pi)]
    report={'ok':False,'failures':[],'cases':{},
            'host_sha256':hashlib.sha256(args.host.read_bytes()).hexdigest(),
            'limits':'Actual Schedule I collision with controlled push input; does not measure Unity stutters or restore captured contact history.'}
    try:
        with engine(args.host,args.out.with_suffix('.log')) as (control,_):
            for road,path,axis,fixed,low,high,up_heading in roads:
                fixture=PROJECT/path
                with fixture.open('rb') as source: header=json.loads(source.readline())
                payload=load_payload(fixture.parent,{'geometry_file':fixture.name,'request':header})
                for direction,start,end,heading in (('uphill',low,high,up_heading),('downhill',high,low,up_heading+math.pi)):
                    name=f'{road}_{direction}'
                    spawn=[fixed,10.,fixed];spawn[axis]=start
                    spawn[1]=ground_height(payload,spawn)
                    control.send({'op':'activate_packed','count':len(payload)//36,'spawn':spawn,'heading':heading,'aspect':16/9},payload)
                    for _ in range(60): step(control)
                    # Settling on a slope can roll the board downhill before controlled input begins.
                    control.send({'op':'resume','spawn':spawn,'heading':heading,'aspect':16/9,'velocity':[0,0,0]})
                    case={'spawn':spawn,'target_axis':axis,'target_coordinate':end,'trace':[],'ground_samples':[],'reached':False,
                          'fixture':str(fixture),'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest()}
                    report['cases'][name]=case
                    for tick in range(360):
                        frame,entry=step(control,buttons=4096);case['trace'].append(entry)
                        if tick%15==0:
                            floor=ground_height(payload,entry['board'])
                            case['ground_samples'].append({'tick':frame['tick'],'clearance_m':entry['board'][1]-floor})
                        if (entry['root'][axis]-end)*(end-start)>=0:
                            case['reached']=True;break
                    case.update(largest_tick_speed_loss_mps=riding_speed_loss(case['trace']),
                                elapsed_seconds=len(case['trace'])*frame['period'],
                                min_board_clearance_m=min(s['clearance_m'] for s in case['ground_samples']),
                                max_board_clearance_m=max(s['clearance_m'] for s in case['ground_samples']),
                                final_root=entry['root'],height_change_m=entry['root'][1]-spawn[1])
                    check(report,case['reached'],f'{name}: did not traverse the 12 metre road in six seconds while pushing')
                    check(report,case['largest_tick_speed_loss_mps']<1.5,f'{name}: abrupt loss of riding momentum')
                    check(report,case['min_board_clearance_m']>-.12,f'{name}: penetrated the road')
                    check(report,case['max_board_clearance_m']<.2,f'{name}: bounced above the road')
                    check(report,all(s['state']=='PhysicsGround' for s in case['trace']),f'{name}: left normal riding on an unobstructed road')
    except Exception as error:
        report['failures'].append(f'{type(error).__name__}: {error}')
    report['ok']=not report['failures']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({**{k:v for k,v in report.items() if k!='cases'},
                      'cases':{name:{k:v for k,v in case.items() if k not in ('trace','ground_samples')} for name,case in report['cases'].items()}},indent=2))
    return 0 if report['ok'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',type=Path,default=PROJECT/'target/debug/skate-schedule')
    parser.add_argument('--out',type=Path,default=PROJECT/'data/hill-report.json')
    args=parser.parse_args();args.host=args.host.resolve()
    raise SystemExit(run(args))
