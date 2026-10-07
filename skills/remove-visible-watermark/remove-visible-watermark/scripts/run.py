#!/usr/bin/env python3
"""Pure classical visible-watermark processing. All paths are local."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import sys
import time
from pathlib import Path

VERSION = '1.3.0'
SKILL_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = SKILL_ROOT / 'assets'
IMAGE_EXTENSIONS = {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',action='version',version=VERSION)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--input',type=Path)
    group.add_argument('--input-dir',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--output-dir',type=Path)
    p.add_argument('--report',type=Path)
    p.add_argument('--profile',default='auto',help='auto, workbuddy, none, profile id, or local JSON path')
    p.add_argument('--mask',type=Path,help='Nonblack nontransparent pixels mark the repair area')
    p.add_argument('--protect-mask',type=Path,help='Nonblack pixels must never be modified')
    p.add_argument('--source-exclude-mask',type=Path,help='Exclude selected pixels from donor patches and reliable matching context; requires --method exemplar and does not expand the repair area')
    p.add_argument('--roi',help='Explicit rectangular repair area x0,y0,x1,y1, right/bottom exclusive')
    p.add_argument('--text-roi',help='Inspect a tight XYXY region and segment pale lettering plus dark outline; does not erase the whole rectangle')
    p.add_argument('--corner-text',action='store_true',help='Try conservative bottom-corner text candidates after a template miss; uncertain regions are skipped')
    p.add_argument('--method',choices=['auto','inverse','fill','exemplar','telea','ns'],default='auto')
    p.add_argument('--quality',choices=['fast','balanced','high'],default='balanced',help='Bounded exemplar patch/search size; high does not guarantee higher accuracy')
    p.add_argument('--refine',choices=['none','consensus'],default='none',help='Structure-guided original-donor consensus; requires --method exemplar. Results still require visual review')
    p.add_argument('--radius',type=float,default=3.0)
    p.add_argument('--dilate',type=int,default=0,help='Explicit expansion of a manual repair mask in pixels')
    p.add_argument('--scale',type=float,help='Explicit watermark-template scale')
    p.add_argument('--max-pixels',type=int,default=40_000_000)
    p.add_argument('--max-workers',type=int,default=2)
    p.add_argument('--recursive',action='store_true')
    p.add_argument('--overwrite',action='store_true')
    p.add_argument('--no-preview',action='store_true')
    p.add_argument('--strict-classical',action='store_true',default=True,
                   help='All processing is always classical, even when this flag is omitted')
    p.add_argument('--list-profiles',action='store_true')
    return p


def emit(value, *, error=False):
    stream = sys.stderr if error else sys.stdout
    print(json.dumps(value,ensure_ascii=False,allow_nan=False),file=stream,flush=True)


def load_runtime():
    try:
        import cv2
        import numpy as np
        import core
        import detect
        import text_mask
        import restore as restoration
    except ImportError as exc:
        raise RuntimeError('Missing image dependency. Run: python scripts/bootstrap.py --install; '
                           'then use the returned python path to invoke scripts/run.py. '
                           f'Detail: {exc}') from exc
    cv2.setNumThreads(1)
    return np,cv2,core,detect,restoration


def reserve_paths(output, report, no_preview):
    output = output.expanduser().resolve()
    if output.suffix.lower() != '.png':
        raise ValueError('Output must use .png so pixel-scope validation is lossless.')
    report = report.expanduser().resolve() if report else output.with_suffix('.report.json')
    paths = {'output':output,'report':report,'mask':output.with_suffix('.mask.png')}
    paths['selection']=output.with_suffix('.selection.png')
    if not no_preview:
        paths['comparison'] = output.with_suffix('.comparison.png')
        paths['selection_preview']=output.with_suffix('.selection-preview.png')
    if len({str(p).casefold() for p in paths.values()}) != len(paths):
        raise ValueError('Output, report, mask and preview paths must be different.')
    return paths


def check_destinations(paths, protected, overwrite):
    reserved = {p.expanduser().resolve() for p in protected if p is not None}
    for p in paths.values():
        if p in reserved:
            raise ValueError(f'Refusing to overwrite an input or Skill resource: {p}')
        if p.exists() and not overwrite:
            raise FileExistsError(f'Output already exists: {p}; select another output or use --overwrite.')


def public_detection(value, core):
    return core.json_safe({k:v for k,v in value.items() if k not in {'alpha','write_mask'}})


def process_one(input_path, output_path, args, runtime, protected, report_path=None):
    np,cv2,core,detect,restoration = runtime
    start = time.perf_counter()
    paths = reserve_paths(output_path,report_path,args.no_preview)
    check_destinations(paths,protected,args.overwrite)
    record = core.load_image(input_path,args.max_pixels)
    rgb = np.ascontiguousarray(record.rgba[:,:,:3])
    shape = rgb.shape[:2]
    manual = args.mask is not None or args.roi is not None or args.text_roi is not None
    roi = core.validate_roi(args.roi,shape) if args.roi else None
    text_roi=core.validate_roi(args.text_roi,shape) if args.text_roi else None
    if args.mask:
        mask = core.load_mask(args.mask,shape)
    elif roi:
        mask = np.zeros(shape,bool)
        x0,y0,x1,y1=roi
        mask[y0:y1,x0:x1] = True
    else:
        mask = np.zeros(shape,bool)
    if args.dilate:
        if not manual:
            raise ValueError('--dilate applies only to an explicit --mask or --roi.')
        mask = core.dilate(mask,args.dilate)
    detection_start=time.perf_counter()
    # An explicit manual mask owns the repair area. Auto does not silently replace
    # it with some other platform mark detected elsewhere in the image.
    use_detector = text_roi is None and (not manual or args.method=='inverse' or args.profile not in {'auto','none'})
    if use_detector:
        detection = detect.find_watermark(rgb,PROFILES_DIR,profile=args.profile,roi=roi,scale=args.scale)
    else:
        detection = {'found':False,'diagnostics':{'reason':'Explicit manual repair area; no automatic profile substitution.'}}
    generic=False
    if text_roi is not None or (args.corner_text and not manual and not detection.get('found')):
        import text_mask
        template_attempt=public_detection(detection,core)
        detection=text_mask.find_text_watermark(rgb,roi=text_roi)
        detection['kind']='pale_text_region'
        detection['template_attempt']=template_attempt
        if detection.get('found'):
            gx0,gy0,gx1,gy1=detection['bbox']
            mask[gy0:gy1,gx0:gx1]=detection['write_mask']
            generic=True
        elif text_roi is not None:
            reason=detection.get('diagnostics',{}).get('reason','no text mask')
            raise ValueError('No reliable pale-text row in --text-roi ('+reason+'); tighten the region or supply an exact --mask. No rectangular erasure was performed.')
    detection_ms = (time.perf_counter()-detection_start)*1000
    selected = None
    if detection.get('found') and not generic:
        selected = dict(detection)
        selected['accepted'] = True
        x0,y0,x1,y1=selected['bbox']
        template_mask = np.zeros(shape,bool)
        template_mask[y0:y1,x0:x1] = selected['write_mask']
        if manual:
            if not np.any(mask & template_mask):
                raise ValueError('Detected watermark does not overlap the explicit repair area.')
            if args.method in {'auto','inverse'}:
                # Use only the matched overlay when inverse restoration is selected.
                mask &= template_mask
        else:
            mask = template_mask
        if np.any(record.rgba[:,:,3][mask] != 255) and args.method in {'auto','inverse'}:
            raise ValueError('Template inversion assumes an opaque backdrop; supply a manual mask with telea/ns for this transparent region.')
    # Keep the complete contamination mask before subtracting protected pixels;
    # protected lettering must never become source texture for another letter.
    source_exclusion=mask.copy() | (record.rgba[:,:,3]==0)
    # Segmented anti-aliased text has uncertain edge pixels. Exclude a small
    # guard from donors without silently enlarging the authorized write area.
    source_guard=2 if generic else 0
    if source_guard:
        source_exclusion |= core.dilate(mask,source_guard)
    explicit_source_pixels=0
    if args.source_exclude_mask:
        extra_source_exclusion=core.load_mask(args.source_exclude_mask,shape)
        explicit_source_pixels=int(extra_source_exclusion.sum())
        source_exclusion |= extra_source_exclusion
    if args.protect_mask:
        mask &= ~core.load_mask(args.protect_mask,shape)
    mask &= record.rgba[:,:,3] > 0
    if args.method=='inverse' and selected is None:
        raise ValueError('Inverse restoration requires a matched, accepted alpha profile.')
    if args.method != 'auto' and not manual and selected is None and not generic:
        raise ValueError('No supported watermark matched; provide --mask or --roi for an explicit repair.')
    process_start=time.perf_counter()
    if not np.any(mask):
        output_rgb=rgb.copy()
        write_mask=np.zeros(shape,bool)
        diag={'method':'none','restoration_kind':'unchanged',
              'assumptions':[], 'warnings':[], 'numeric_out_of_range_fraction':0.0}
        status='skipped_no_match' if not manual else 'skipped_empty_mask'
    else:
        output_rgb,write_mask,diag=restoration.restore(rgb,mask,method=args.method,detection=selected,radius=args.radius,
                                                     source_exclusion=source_exclusion,quality=args.quality,refinement=args.refine)
        if generic:
            diag['needs_review']=True
            diag['warnings'].extend(detection.get('diagnostics',{}).get('warnings',[]))
            diag['assumptions'].append('The requested text region identifies the watermark; morphology estimates its full stroke and outline support.')
        if output_rgb.shape!=rgb.shape or output_rgb.dtype!=np.uint8 or write_mask.shape!=shape:
            raise ValueError('Backend returned an invalid image or mask.')
        write_mask=np.asarray(write_mask,dtype=bool)
        if np.any(write_mask & ~mask):
            raise ValueError('Backend attempted to expand the authorized write mask.')
        if not np.array_equal(output_rgb[~write_mask],rgb[~write_mask]):
            raise ValueError('Backend changed pixels outside its declared write mask.')
        status='needs_review' if diag.get('needs_review') else 'processed'
    algorithm_ms=(time.perf_counter()-process_start)*1000
    result=record.rgba.copy()
    result[write_mask,:3]=output_rgb[write_mask]
    core.save_png(paths['output'],result,record.output_mode,record.info,args.overwrite)
    validation=core.validate_pixels(record,paths['output'],write_mask)
    core.save_png(paths['mask'],write_mask.astype(np.uint8)*255,mode='L',overwrite=args.overwrite)
    core.save_png(paths['selection'],mask.astype(np.uint8)*255,mode='L',overwrite=args.overwrite)
    if 'comparison' in paths:
        core.comparison_png(paths['comparison'],record.rgba,result,write_mask,args.overwrite)
        core.selection_preview_png(paths['selection_preview'],record.rgba,mask,args.overwrite)
    report={
        'schema_version':1,'skill':'remove-visible-watermark','version':VERSION,
        'status':status,'method':diag.get('method'),'strict_classical':True,
        'input':{'path':str(record.path),'sha256':record.sha256,
                 'encoded_size':list(record.source_size),'upright_size':[shape[1],shape[0]],
                 'source_mode':record.source_mode,'exif_orientation_normalized':record.orientation_normalized},
        'output':str(paths['output']),
        'detection':public_detection(detection,core),
        'restoration':core.json_safe(diag),
        'validation':validation,
        'write_mask':{'bbox_xyxy_exclusive':core.bbox(write_mask),'path':str(paths['mask'])},
        'selection_mask':{'bbox_xyxy_exclusive':core.bbox(mask),'pixels':int(mask.sum()),'path':str(paths['selection']),
                          'source_excluded_pixels':int(source_exclusion.sum())},
        'source_exclusion':{'explicit_mask':str(args.source_exclude_mask.expanduser().resolve()) if args.source_exclude_mask else None,
                            'explicit_pixels':explicit_source_pixels,'total_pixels':int(source_exclusion.sum()),'text_edge_guard_pixels':source_guard,
                            'policy':'Exclude complete watermark, transparent pixels and explicit source exclusion from intact donors and reliable matching context.'},
        'artifacts':{k:str(v) for k,v in paths.items()},
        'timings_ms':{'detection':detection_ms,'restoration':algorithm_ms,'total_before_report':(time.perf_counter()-start)*1000},
        'true_original_accuracy':None,
        'quality_note':'Pixel-scope verification is not proof of original-background accuracy; score fields are heuristic diagnostics, not probabilities.',
    }
    core.atomic_json(paths['report'],report,args.overwrite)
    return {'input':str(record.path),'output':str(paths['output']),'report':str(paths['report']),
            'status':status,'method':diag.get('method'),'validation':validation}


def error_result(input_path,output_path,exc):
    return {'input':str(input_path),'output':str(output_path),'status':'error',
            'error':{'type':type(exc).__name__,'message':str(exc)}}


def main(argv=None):
    p=parser()
    args=p.parse_args(argv)
    if args.list_profiles:
        profiles=[]
        for path in sorted(PROFILES_DIR.glob('*.json')):
            data=json.loads(path.read_text(encoding='utf-8'))
            profiles.append({'id':data.get('id'),'path':str(path),
                             'calibration':data.get('calibration'),'notes':data.get('notes')})
        emit({'version':VERSION,'profiles':profiles})
        return 0
    if args.input is None and args.input_dir is None:
        p.error('Provide --input or --input-dir.')
    runtime=None
    protected=[p.resolve() for p in SKILL_ROOT.rglob('*') if p.is_file()]
    protected.extend(p for p in [args.input,args.mask,args.protect_mask,args.source_exclude_mask] if p is not None)
    try:
        runtime=load_runtime()
        if sum(v is not None for v in [args.mask,args.roi,args.text_roi])>1:
            raise ValueError('Use only one of --mask, --roi or --text-roi.')
        if args.corner_text and any(v is not None for v in [args.mask,args.roi,args.text_roi]):
            raise ValueError('--corner-text is an optional automatic candidate mode; do not combine it with a manual region.')
        if args.text_roi is not None and (args.method=='inverse' or args.dilate):
            raise ValueError('--text-roi builds its own bounded stroke mask; use --mask for explicit dilation or a calibrated --profile for inverse.')
        if args.source_exclude_mask and args.method!='exemplar':
            raise ValueError('--source-exclude-mask requires --method exemplar so its donor restriction cannot be lost through an automatic fallback.')
        if args.refine!='none' and args.method!='exemplar':
            raise ValueError('--refine consensus requires --method exemplar; refinement is explicit and never silently replaced with another method.')
        if not math.isfinite(args.radius) or not 1<=args.radius<=15:
            raise ValueError('--radius must be finite and between 1 and 15.')
        if args.scale is not None and (not math.isfinite(args.scale) or not 0.125<=args.scale<=8):
            raise ValueError('--scale must be finite and between 0.125 and 8.')
        if not 0<=args.dilate<=64 or args.max_pixels<=0 or not 1<=args.max_workers<=8:
            raise ValueError('Invalid limit: dilate 0..64, positive max-pixels, max-workers 1..8.')
        np,cv2,core,detect,restoration=runtime
        profile_path=Path(args.profile).expanduser()
        if profile_path.is_file():
            protected.append(profile_path)
            # Protect all profile-local assets, even if configuration validation later fails.
            protected.extend(p for p in profile_path.parent.iterdir() if p.is_file())
        if args.input:
            if args.recursive:
                raise ValueError('--recursive is a batch option.')
            if args.output and args.output_dir:
                raise ValueError('Choose --output or --output-dir for a single input.')
            output=args.output or (args.output_dir or Path.cwd()/'watermark-output')/(args.input.stem+'_clean.png')
            protected.append(args.input)
            result=process_one(args.input,output,args,runtime,protected,args.report)
            emit(result)
            return 0
        if args.output or args.mask or args.protect_mask or args.source_exclude_mask or args.roi or args.text_roi or args.report:
            raise ValueError('Batch mode uses --output-dir and per-image sidecars; manual mask/ROI/report options require a single input.')
        input_dir=args.input_dir.expanduser().resolve(strict=True)
        if not input_dir.is_dir():
            raise ValueError('--input-dir must be a directory.')
        output_dir=(args.output_dir or input_dir.parent/(input_dir.name+'_cleaned')).expanduser().resolve()
        if output_dir==input_dir:
            raise ValueError('Batch output directory must differ from the input directory.')
        paths=sorted((input_dir.rglob('*') if args.recursive else input_dir.iterdir()),key=lambda p:str(p).casefold())
        sources=[p for p in paths if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
                 and not p.resolve().is_relative_to(output_dir)]
        if not sources:
            raise ValueError('No supported static image files found in the input directory.')
        targets=[]
        for source in sources:
            relative=source.relative_to(input_dir)
            targets.append(output_dir/relative.parent/(relative.stem+'__'+relative.suffix[1:].lower()+'_clean.png'))
        summary_path=output_dir/'summary.json'
        all_destinations=[summary_path]
        for target in targets:
            all_destinations.extend(reserve_paths(target,None,args.no_preview).values())
        if len({str(p).casefold() for p in all_destinations})!=len(all_destinations):
            raise ValueError('Batch output names collide on a case-insensitive filesystem; rename the conflicting inputs.')
        protected.extend(sources)
        check_destinations({str(i):v for i,v in enumerate(all_destinations)},protected,args.overwrite)
        results=[None]*len(sources)
        # Fixed outer concurrency + one OpenCV thread per operation; deterministic file ordering.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.max_workers,len(sources))) as pool:
            pending={pool.submit(process_one,src,dst,args,runtime,protected):i
                     for i,(src,dst) in enumerate(zip(sources,targets))}
            for future in concurrent.futures.as_completed(pending):
                index=pending[future]
                try:
                    results[index]=future.result()
                except Exception as exc:
                    results[index]=error_result(sources[index],targets[index],exc)
                    failure_path=targets[index].with_suffix('.report.json')
                    core.atomic_json(failure_path,results[index],args.overwrite)
                    results[index]['report']=str(failure_path)
        failures=sum(r['status']=='error' for r in results)
        summary={'schema_version':1,'version':VERSION,'strict_classical':True,
                 'status':'completed_with_errors' if failures else 'completed',
                 'count':len(results),'errors':failures,'results':results}
        core.atomic_json(summary_path,summary,args.overwrite)
        emit({'status':summary['status'],'count':len(results),'errors':failures,'summary':str(summary_path)})
        return 1 if failures else 0
    except Exception as exc:
        value=error_result(args.input or args.input_dir,args.output or args.output_dir or '',exc)
        # Explicit, safe error reports are useful to batch/orchestrating agents.
        # Never replace input, mask, profile resources, or the requested image with JSON.
        if args.report and runtime is not None:
            try:
                core=runtime[2]
                error_path=args.report.expanduser().resolve()
                reserved=list(protected)
                if args.output:
                    reserved.append(args.output)
                profile_path=Path(args.profile).expanduser()
                if profile_path.is_file():
                    reserved.extend(p for p in profile_path.parent.iterdir() if p.is_file())
                check_destinations({'report':error_path},reserved,args.overwrite)
                core.atomic_json(error_path,value,args.overwrite)
                value['report']=str(error_path)
            except Exception as report_exc:
                value['report_not_written']=str(report_exc)
        emit(value,error=True)
        return 2


if __name__=='__main__':
    sys.exit(main())
