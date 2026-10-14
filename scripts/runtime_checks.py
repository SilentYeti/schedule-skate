"""Summarize actual Unity frame/ground/dismount recordings; never launch or alter the game."""
from pathlib import Path
import argparse
import json
from headless import summary


def analyze(path):
    # The background writer may be halfway through its last row during a live read.
    events=[json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith('\n') and line.strip()]
    records=[event['record'] for event in events]
    frames=[r for r in records if r['kind']=='frame_time']
    baseline=[r for r in records if r['kind']=='native_frame_time' and not r.get('preparing',False)]
    ground=[r for r in records if r['kind']=='frame' and r.get('ground_y') is not None]
    replies=[r for r in records if r['kind']=='reply']
    grouped={}
    for r in replies:
        grouped.setdefault(r['request']['op'],[]).append(r['ms'])
    penetrations=[r for r in ground if r['board'][1]<r['ground_y']-.3]
    rider_ground=[r for r in records if r['kind']=='frame' and r.get('rider_ground_y') is not None]
    return {'events':len(events),'frames':len(frames),
            'frame_ms':summary([r['dt']*1000 for r in frames]) if frames else None,
            'frames_over_50ms':sum(r['dt']>.05 for r in frames),
            'native_frame_ms':summary([r['dt']*1000 for r in baseline]) if baseline else None,
            'mod_update_ms':summary([r['ms'] for r in records if r['kind']=='update_time']) if any(r['kind']=='update_time' for r in records) else None,
            'presentation_ms':{name:summary([r[name] for r in records if r['kind']=='presentation_time'])
                               for name in ('blend_ms','board_ms','avatar_ms','ground_ms','camera_ms')}
                              if any(r['kind']=='presentation_time' for r in records) else {},
            'pose_age_ms':summary([r['pose_age']*1000 for r in frames]) if frames else None,
            'requests':{op:summary(values) for op,values in grouped.items()},
            'snapshot_batches':summary([r['ms'] for r in records if r['kind']=='snapshot_batch']) if any(r['kind']=='snapshot_batch' for r in records) else None,
            'ground_samples':len(ground),'below_ground_samples':penetrations[:50],
            'rider_ground_samples':len(rider_ground),
            'rider_below_ground_samples':[r for r in rider_ground if r['rider'][1]<r['rider_ground_y']-.3][:50],
            'focus_outside_export_samples':[r for r in records if r['kind']=='frame' and r.get('collision_focus_distance',0)>24][:50],
            'empty_overlaps':[r for r in records if r['kind']=='snapshot_overlap' and r['count']==0],
            'errors':[r for r in records if r['kind']=='bridge_error'],
            'native_dismounts':[r for r in records if r['kind']=='native_dismount'],
            'prevented_angle_dismounts':sum(r['kind']=='prevented_angle_dismount' for r in records),
            'interpolation_fallbacks':[r for r in records if r['kind']=='interpolation_fallback'],
            'state_changes':[r for r in records if r['kind']=='state_change'],
            'geometry':[r['file'] for r in records if r['kind']=='geometry'],
            'limits':'Unity raycasts identify candidate penetration; a crossing platform/ceiling can also be above the board. No ground hit does not prove a hole. Recording is bounded to 40000 rows.'}


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'runtime.jsonl'
        rows=[{'kind':'frame_time','dt':.1,'pose_age':.04},
              {'kind':'frame','board':[0,-1,0],'ground_y':0,'rider':[0,-2,0],'rider_ground_y':0,'collision_focus_distance':30},
              {'kind':'reply','request':{'op':'resume'},'ms':20},
              {'kind':'native_dismount','state':'KnownAir'}]
        path.write_text(''.join(json.dumps({'at_ms':i,'record':row})+'\n' for i,row in enumerate(rows)))
        report=analyze(path)
        assert report['frames_over_50ms']==1 and len(report['below_ground_samples'])==1
        assert report['requests']['resume']['max_ms']==20 and len(report['native_dismounts'])==1
        assert len(report['rider_below_ground_samples'])==1 and len(report['focus_outside_export_samples'])==1
        with path.open('a') as output:
            for row in ({'kind':'native_frame_time','dt':.02},
                        {'kind':'native_frame_time','dt':.6,'preparing':True},
                        {'kind':'update_time','ms':2},
                        {'kind':'presentation_time',**{name:3 for name in ('blend_ms','board_ms','avatar_ms','ground_ms','camera_ms')}}):
                output.write(json.dumps({'at_ms':4,'record':row})+'\n')
        report=analyze(path)
        assert report['native_frame_ms']['count']==1 and report['native_frame_ms']['max_ms']==20
        assert report['mod_update_ms']['max_ms']==2 and report['presentation_ms']['avatar_ms']['max_ms']==3
        with path.open('a') as output: output.write('{"at_ms":')
        assert analyze(path)['events']==8
        path.write_text(''); assert analyze(path)['frame_ms'] is None
    print('Runtime trace analyzer checks passed.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path,nargs='?')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--self-check',action='store_true')
    args=parser.parse_args()
    if args.self_check: self_check()
    else:
        if args.trace is None: parser.error('trace path required')
        report=analyze(args.trace)
        result=json.dumps(report,indent=2,allow_nan=False)+'\n'
        if args.out: args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(result)
        print(result)
