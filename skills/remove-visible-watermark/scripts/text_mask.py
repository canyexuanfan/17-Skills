"""Classical pale text / dark outline mask, no OCR or templates.
All thresholds are heuristic; semantic watermark identity is not determined.
"""
from __future__ import annotations
import cv2
import numpy as np


def _components(mask):
    n,labels,stats,centers=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    return labels,stats[1:],centers[1:]


def _dark_ridge(gray, radius=3):
    """Find narrow dark contours with a rise on both sides, not one-sided steps."""
    h,w=gray.shape
    values=cv2.copyMakeBorder(gray.astype(np.float32),radius,radius,radius,radius,
                             cv2.BORDER_REFLECT_101)
    center=values[radius:radius+h,radius:radius+w]
    ridge=np.zeros_like(center)
    for step in range(1,radius+1):
        for dy,dx in ((0,1),(1,0),(1,1),(1,-1)):
            a=values[radius+dy*step:radius+dy*step+h,radius+dx*step:radius+dx*step+w]
            b=values[radius-dy*step:radius-dy*step+h,radius-dx*step:radius-dx*step+w]
            ridge=np.maximum(ridge,np.minimum(a-center,b-center))
    return ridge


def _segment(rgb, box, explicit, _kernel=None, _initial_kernel=None):
    x0,y0,x1,y1=map(int,box)
    crop=rgb[y0:y1,x0:x1]
    ch,cw=crop.shape[:2]
    if min(ch,cw)<12:return None
    # Read a bounded halo so moving the authorized write boundary does not
    # change the median filter's padding around the same image pixels. All
    # classification and output below remain clipped to the original ROI.
    margin=32  # Covers the largest (55 px) median kernel's 27 px radius.
    cx0,cy0=max(0,x0-margin),max(0,y0-margin)
    cx1,cy1=min(rgb.shape[1],x1+margin),min(rgb.shape[0],y1+margin)
    context=rgb[cy0:cy1,cx0:cx1]
    context_gray=cv2.cvtColor(context,cv2.COLOR_RGB2GRAY)
    context_chroma=context.max(2)-context.min(2)
    sy,sx=y0-cy0,x0-cx0
    def median_context(channel,size):
        filtered=cv2.medianBlur(channel,size)
        return filtered[sy:sy+ch,sx:sx+cw].astype(np.float32)
    # ROI height supplies only an initial guess. A consistent glyph row may
    # supply one bounded second pass at a scale inferred from its own height.
    k=int(_kernel) if _kernel is not None else max(9,min(41,int(round(ch*.32))|1))
    initial_k=int(_initial_kernel) if _initial_kernel is not None else k
    gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY).astype(np.float32)
    base=median_context(context_gray,k)
    chroma=crop.max(2).astype(np.float32)-crop.min(2).astype(np.float32)
    cb=median_context(context_chroma,k)
    residual=gray-base
    # MAD measures fine texture and JPEG variation; cap conservatively for text strokes.
    local=median_context(context_gray,3)
    sigma=1.4826*float(np.median(np.abs(gray-local)))
    bright_t=float(np.clip(4+3*sigma,10,18))
    dark_t=float(np.clip(5+3*sigma,11,19))
    seeds=(residual>bright_t)|((cb-chroma>12)&(gray>base-8))
    raw_seeds=seeds.copy()
    # A one-pixel contrast bridge can attach a long scene highlight to a
    # letter, corrupting the row estimate. Separate such bridges before row
    # fitting, then restore nearby original stroke support after fitting.
    # Very thin lettering lacks a substantial interior; leave that case intact.
    seed_distance=cv2.distanceTransform(seeds.astype(np.uint8),cv2.DIST_L2,3)
    interior_fraction=float(np.mean(seed_distance[seeds]>=1.9)) if seeds.any() else 0.
    split_bridges=interior_fraction>=.20
    if split_bridges:
        seeds=cv2.morphologyEx(seeds.astype(np.uint8),cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3))).astype(bool)
    bridge_pixels=int((raw_seeds&~seeds).sum())
    labs,stats,centers=_components(seeds)
    # Large foreground parts give a text baseline; remove broad scenery and top-edge fragments.
    tall=[]
    for i,(xx,yy,ww,hh,area) in enumerate(stats):
        if area>=max(14,ch*.14) and max(11,ch*.28)<=hh<=ch*.9 and 2<=ww<=cw*.42 and .08<=area/(ww*hh)<=.95:
            tall.append(i)
    if len(tall)<2:return None
    heights=np.array([stats[i,3] for i in tall]);
    mh=float(np.median(heights))
    tall=[i for i in tall if .55*mh<=stats[i,3]<=1.65*mh]
    if len(tall)<2:return None
    # Dominant row consensus around lower ends of tall letters.
    best=[]
    tol=max(3,mh*.2)
    for i in tall:
        bottom=stats[i,1]+stats[i,3]
        group=[j for j in tall if abs(stats[j,1]+stats[j,3]-bottom)<=tol]
        if len(group)>len(best):best=group
    if len(best)<2:return None
    # Median tops and bottoms keep one attached high fragment from setting
    # the scale for the whole row. This is a glyph estimate, not a brand size.
    geometry_h=float(np.median([stats[i,1]+stats[i,3] for i in best])-
                     np.median([stats[i,1] for i in best]))
    geometry_k=max(9,min(41,int(round(geometry_h*.5))|1))
    if _kernel is None and geometry_k!=k:
        refined=_segment(rgb,box,explicit,_kernel=geometry_k,_initial_kernel=k)
        if refined is not None:return refined
    left=min(int(stats[i,0]) for i in best);right=max(int(stats[i,0]+stats[i,2]) for i in best)
    top=float(np.percentile([stats[i,1] for i in best],15));bottom=float(np.median([stats[i,1]+stats[i,3] for i in best]))
    line_h=bottom-top
    if line_h<10:return None
    kept=np.zeros(seeds.shape,bool)
    selected=[]
    for i,(xx,yy,ww,hh,area) in enumerate(stats):
        cx,cy=centers[i]
        if area<6 or ww>cw*.65 or hh>1.8*line_h:continue
        # Retain small detached letter strokes only within the consensus row.
        if cy < top+0.5 or cy>bottom+2 or yy+hh<top+2 or yy>bottom:continue
        # A narrow first/last stem (for example T or I) may be detached from its
        # horizontal stroke and excluded from the tall-component consensus.
        # An explicit, tight ROI can include those edge stems without trusting
        # equally wide expansion during blind corner proposals.
        left_margin=right_margin=.8 if explicit else None
        if not explicit:left_margin,right_margin=.4,.6
        if xx+ww < left-line_h*left_margin or xx>right+line_h*right_margin:continue
        kept[labs==i+1]=True; selected.append(i)
    if not kept.any():return None
    # Restore only original bright/neutral support near the selected glyphs.
    # Do not reconnect an arbitrary length of the old scene-edge component.
    reconstruction_radius=max(1,min(3,int(round(line_h*.06))))
    if split_bridges:
        p=2*reconstruction_radius+1
        stroke_near=cv2.dilate(kept.astype(np.uint8),cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(p,p))).astype(bool)
        kept |= raw_seeds&stroke_near
    # Add signed luminance anomalies connected to text, including dark outline.
    r=max(3,min(7,int(round(line_h*.12))))
    near=cv2.dilate(kept.astype(np.uint8),cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*r+1,2*r+1))).astype(bool)
    broad=median_context(context_gray,max(k,min(55,int(line_h*.7)|1)))
    broad_chroma=median_context(context_chroma,max(k,min(55,int(line_h*.7)|1)))
    ink=((residual < -max(6,dark_t*.6)) | (gray-broad < -8) | ((broad_chroma-chroma>8)&(gray<broad+8)))
    mask=kept | (ink&near)
    # A low-contrast thin hook can lose its bright core while its dark outline
    # remains measurable just beyond the normal stroke radius. Continue only
    # short, connected, two-sided dark ridges; merely widening the radius would
    # also admit an unrelated one-sided scene edge.
    extra=max(1,min(3,int(round(line_h*.06))))
    reach=cv2.dilate(kept.astype(np.uint8),cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                                  (2*(r+extra)+1,)*2)).astype(bool)
    dark_ridge=_dark_ridge(context_gray)[sy:sy+ch,sx:sx+cw]
    # Learn the lower contrast of already observed outline cores, rather than
    # following weaker nearby grain just because it is dark and connected.
    observed_ridges=dark_ridge[ink&near&(dark_ridge>dark_t)]
    continuation_threshold=max(dark_t,float(np.percentile(observed_ridges,25))) if len(observed_ridges)>=16 else float('inf')
    eligible=ink&reach&(dark_ridge>continuation_threshold)
    contour=mask&ink
    for unused in range(extra):
        adjacent=cv2.dilate(contour.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
        contour |= eligible&adjacent
    outline_extension=contour&~mask
    # Only the newly supported dark contour may admit one weak antialias ring;
    # this does not start a new trace on an isolated background mark.
    fringe=cv2.dilate(outline_extension.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
    outline_extension |= fringe&ink&reach&(dark_ridge>=max(3,2*sigma))&~mask
    mask |= outline_extension
    # Full signed anomalies improve thin outline coverage; constrain to learned text row.
    band=np.zeros(mask.shape,bool)
    # A consensus baseline is not the full glyph extent: descenders, slanted
    # corners and outline AA can extend beyond the median row by several pixels.
    row_margin=max(3,min(9,int(np.ceil(line_h*.12))))
    bt=max(0,int(np.floor(top))-row_margin);bb=min(ch,int(np.ceil(bottom))+row_margin)
    band[bt:bb,:]=True;mask &=band
    evidence=mask.copy()
    # Cover outline anti-alias pixels without erasing all interletter gaps.
    mask=cv2.morphologyEx(mask.astype(np.uint8),cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)))
    mask=cv2.dilate(mask,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3))).astype(bool)&band
    # Morphology alone must not grow pale lettering over a much darker object
    # edge. Keep observed ink; constrain only the extra closure/dilation pixels.
    nearby_ink_min=cv2.erode(np.where(evidence,gray,255).astype(np.float32),np.ones((5,5),np.uint8))
    unsafe_growth=(~evidence)&(gray<nearby_ink_min-max(12,3*sigma))
    rejected_growth=int((mask&unsafe_growth).sum())
    mask &= ~unsafe_growth
    # Fill enclosed uncertain gaps on a glyph-size budget. This intentionally includes
    # some background inside letters, while open interletter gaps remain intact.
    inv=(~mask).astype(np.uint8);n,il,ss,_=cv2.connectedComponentsWithStats(inv,8)
    for i in range(1,n):
        xx,yy,ww,hh,area=ss[i]
        if area<=max(6,line_h*line_h*.22) and ww<=line_h*.7 and hh<=line_h and xx>0 and yy>0 and xx+ww<cw and yy+hh<ch:mask[il==i]=True
    yy,xx=np.nonzero(mask)
    if not len(xx):return None
    bx0,bx1=int(xx.min()),int(xx.max()+1);by0,by1=int(yy.min()),int(yy.max()+1)
    bounds=(x0+bx0,y0+by0,x0+bx1,y0+by1)
    trim=mask[by0:by1,bx0:bx1]
    ratio=(bx1-bx0)/(by1-by0)
    occupancy=float(trim.mean())
    # Count separated foreground columns after light stroke merging as a weak glyph-layout feature.
    histogram=kept[bt:bb].sum(0)
    segments=[];start=None
    for ii,on in enumerate(histogram>max(1,line_h*.06)):
        if on and start is None:start=ii
        if not on and start is not None:
            if ii-start>=2:segments.append((start,ii))
            start=None
    if start is not None:segments.append((start,len(histogram)))
    height_cv=float(np.std([stats[i,3] for i in best])/(np.mean([stats[i,3] for i in best])+1e-6))
    shape_score=float(np.clip(.45 + .06*len(best) + .02*min(len(segments),6)-.3*height_cv,0,.9))
    if not explicit:
        H,W=rgb.shape[:2]
        margin_bottom=H-bounds[3]
        margin_side=min(bounds[0],W-bounds[2])
        plausible=(bounds[0]>0 and bounds[2]<W and bounds[1]>0 and bounds[3]<H and len(best)>=3 and len(segments)>=3 and 2.8<=ratio<=18 and .1<=occupancy<=.78 and height_cv<=.30 and 8<=by1-by0<=min(H,W)*.06 and margin_bottom<=H*.035 and margin_side<=W*.035)
        if not plausible:return None
    return {'found':True,'bbox':list(bounds),'write_mask':trim,'score':shape_score,
            'diagnostics':{'detector':'pale_text_dark_outline_v3','explicit_roi':bool(explicit),'heuristic_only':True,'needs_review':True,'roi':list(box),'background_context_bbox':list((cx0,cy0,cx1,cy1)),'initial_median_kernel':initial_k,'median_kernel':k,'geometry_height':geometry_h,'geometry_kernel_target':geometry_k,'geometry_refinement_performed':_kernel is not None,'seed_bridge_split_applied':split_bridges,'seed_bridge_pixels':bridge_pixels,'seed_reconstruction_radius':reconstruction_radius if split_bridges else 0,'outline_continuation_pixels':int((outline_extension&band).sum()),'outline_continuation_extra_radius':extra,'noise_estimate':sigma,'bright_threshold':bright_t,'dark_threshold':dark_t,'row_height':line_h,'row_margin':row_margin,'rejected_dark_edge_growth_pixels':rejected_growth,'tall_components':len(best),'column_segments':len(segments),'aspect_ratio':ratio,'mask_occupancy':occupancy,'height_cv':height_cv,'warnings':['No OCR or semantic watermark identification.','Pale text and dark outline may be confused with image content.','The mask is an estimate; complex backgrounds and very low contrast can leave uncertain pixels.','Enclosed small glyph gaps may include some real background to cover low-contrast stroke interiors.']}}


def find_text_watermark(rgb, roi=None):
    """Return a local boolean write_mask and global exclusive XYXY bbox.
    Explicit ROI bounds writes; a 32-pixel read halo supports filtering. Without it, only
    conservative bottom-corner candidates are tested and ambiguous hits skipped.
    """
    if not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3:raise ValueError('RGB uint8 HxWx3 required')
    H,W=rgb.shape[:2]
    if roi is not None:
        if len(roi)!=4:raise ValueError('ROI requires four integers')
        if any(not isinstance(v,(int,np.integer)) for v in roi):raise ValueError('ROI coordinates must be integers')
        x0,y0,x1,y1=map(int,roi)
        if not (0<=x0<x1<=W and 0<=y0<y1<=H):raise ValueError('ROI out of bounds')
        if (x1-x0)*(y1-y0)>1_000_000:raise ValueError('Text ROI exceeds one million pixels; provide a tighter region around the watermark')
        found=_segment(rgb,(x0,y0,x1,y1),True)
        return found or {'found':False,'diagnostics':{'reason':'no_consistent_pale_text_row','needs_review':True}}
    # One bounded scale per corner. Large/full-frame typography is outside automatic scope.
    rw=min(W,max(80,int(round(W*.26)))); rh=min(H,max(40,int(round(H*.065))))
    hits=[]
    for box in [(0,H-rh,rw,H),(W-rw,H-rh,W,H)]:
        found=_segment(rgb,box,False)
        if found is not None:hits.append(found)
    if len(hits)==1:return hits[0]
    return {'found':False,'diagnostics':{'reason':'ambiguous_corner_text' if len(hits)>1 else 'no_conservative_corner_candidate','candidate_count':len(hits),'needs_review':True}}
