"""Bounded classical refinement guided by background and observed structure.

This NumPy/OpenCV implementation uses only complete original-image donors,
explicit coverage propagation and overlapping gradient consensus. No learned
models or network access are used. Hidden content is estimated, not recovered
with an accuracy guarantee; averaging can soften fine texture.
"""
from __future__ import annotations

import time
import cv2
import numpy as np


MAX_MASK_PIXELS = 40_000
MAX_CONTEXT_PIXELS = 1_000_000
MAX_VOTE_CENTERS = 2_000
MAX_SOLVER_ITERATIONS = 500
MAX_MATCH_LOCATIONS = 128_000_000
PATCH_RADIUS = 9
SEARCH_RADIUS = 112
CENTER_STRIDE = 6
MAX_LINE_PROPOSALS = 128
MAX_OBSERVED_EDGE_PIXELS = 50_000
SOURCE_GUARD = 2
SCREEN_WEIGHT = 0.015
SOLVER_RMS_TOLERANCE = 0.001


def _solve(base, mask, gx, gy, prior, *, screen_weight=SCREEN_WEIGHT):
    """Solve independent donor gradients with fixed observed boundary values.

    Only real image pixels enter the linear system: reflected donor padding
    must not become a false observed boundary at an edge of the original image.
    The three channels use independent preconditioned CG convergence states.
    """
    ys, xs = np.where(mask)
    n = len(ys)
    h, w = mask.shape
    index = np.full((h, w), -1, np.int32)
    index[ys, xs] = np.arange(n, dtype=np.int32)
    degree = np.full(n, screen_weight, np.float64)
    rhs = screen_weight * prior[ys, xs].astype(np.float64)
    rows, cols = [], []
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        ny, nx = ys + dy, xs + dx
        valid = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
        rr = np.flatnonzero(valid)
        ny, nx = ny[valid], nx[valid]
        degree[rr] += 1.0
        if dx == 1:
            gradient = -gx[ys[rr], xs[rr]]
        elif dx == -1:
            gradient = gx[ys[rr], xs[rr] - 1]
        elif dy == 1:
            gradient = -gy[ys[rr], xs[rr]]
        else:
            gradient = gy[ys[rr] - 1, xs[rr]]
        rhs[rr] += gradient
        neighbor = index[ny, nx]
        known = neighbor < 0
        rhs[rr[known]] += base[ny[known], nx[known]]
        rows.append(rr[~known])
        cols.append(neighbor[~known])

    def multiply(values):
        product = degree[:, None] * values
        for rr, cc in zip(rows, cols):
            product[rr] -= values[cc]
        return product

    values = prior[ys, xs].astype(np.float64)
    residual = rhs - multiply(values)
    rms = np.sqrt(np.mean(residual * residual, axis=0))
    if not np.all(np.isfinite(rms)):
        raise ValueError('consensus solver has a non-finite initial residual')
    active = rms > SOLVER_RMS_TOLERANCE
    z = residual / degree[:, None]
    direction = z.copy()
    direction[:, ~active] = 0.0
    rz = np.sum(residual * z, axis=0)
    iterations = 0
    while np.any(active) and iterations < MAX_SOLVER_ITERATIONS:
        iterations += 1
        product = multiply(direction)
        denominator = np.sum(direction * product, axis=0)
        if (not np.all(np.isfinite(denominator[active]))
                or np.any(denominator[active] <= 0.0)
                or np.any(rz[active] <= 0.0)):
            raise ValueError('consensus solver lost a valid positive CG direction')
        alpha = np.zeros(3, np.float64)
        alpha[active] = rz[active] / denominator[active]
        values += direction * alpha
        residual -= product * alpha
        rms = np.sqrt(np.mean(residual * residual, axis=0))
        if not np.all(np.isfinite(rms)) or not np.all(np.isfinite(values)):
            raise ValueError('consensus solver produced non-finite values')
        next_active = rms > SOLVER_RMS_TOLERANCE
        if not np.any(next_active):
            active = next_active
            break
        z = residual / degree[:, None]
        next_rz = np.sum(residual * z, axis=0)
        beta = np.zeros(3, np.float64)
        if np.any(rz[next_active] <= 0.0) or not np.all(np.isfinite(next_rz[next_active])):
            raise ValueError('consensus solver has an invalid CG residual')
        beta[next_active] = next_rz[next_active] / rz[next_active]
        direction = z + direction * beta
        direction[:, ~next_active] = 0.0
        rz, active = next_rz, next_active
    # Verify the true equation residual rather than only the iterative update.
    true_residual = rhs - multiply(values)
    rms = np.sqrt(np.mean(true_residual * true_residual, axis=0))
    if not np.all(np.isfinite(rms)) or np.any(rms > SOLVER_RMS_TOLERANCE):
        raise ValueError('consensus did not converge within its fixed 500-iteration budget')
    out = base.copy()
    out[ys, xs] = values
    return out, {
        'iterations': iterations,
        'rms_residual': float(np.sqrt(np.mean(true_residual * true_residual))),
        'channel_rms_residual': [float(v) for v in rms],
        'rms_tolerance': SOLVER_RMS_TOLERANCE,
        'converged': True,
        'screen_weight': screen_weight,
        'boundary_policy': 'fixed original values outside the write mask; real image edges only',
    }



def _candidate(local, query, weights, center, source, *, inherited_offset=None):
    """Score an intact donor proposal; never promote excluded target samples."""
    ty, tx = center
    sy, sx = source
    r = PATCH_RADIUS
    donor = local[sy-r:sy+r+1, sx-r:sx+r+1]
    target = query[ty-r:ty+r+1, tx-r:tx+r+1]
    mass = float(weights.sum())
    if inherited_offset is None:
        if mass < 3.0:
            raise ValueError('coherent donor has insufficient independent matching support')
        delta = target - donor
        raw_offset = np.sum(delta * weights[..., None], axis=(0, 1)) / mass
        offset = np.clip(raw_offset, -20.0, 20.0)
        error = float(np.sum((delta - offset) ** 2 * weights[..., None]) / mass)
    else:
        # Low-evidence blocks inherit a neighbouring, supported field proposal.
        # They are not matched to a newly trusted subset of excluded pixels.
        offset = inherited_offset.astype(np.float32).copy()
        error = 0.0
    return {'source': (int(sy), int(sx)), 'offset': offset,
            'error': error}


def _observed_line_support(local, mask, excluded):
    """Protect initial structure only along a line observed on both mask sides.

    Image gradients are used only where their complete 3x3 stencil is clean.
    A Hough proposal alone is insufficient: at least eight correctly oriented
    observed edge pixels must support each end of its masked span.
    """
    gray=cv2.cvtColor(np.rint(local).clip(0,255).astype(np.uint8),cv2.COLOR_RGB2GRAY)
    clean=cv2.erode((~excluded).astype(np.uint8),np.ones((3,3),np.uint8),
                    borderType=cv2.BORDER_CONSTANT,borderValue=0)>0
    gx=cv2.Sobel(gray,cv2.CV_32F,1,0,ksize=3)
    gy=cv2.Sobel(gray,cv2.CV_32F,0,1,ksize=3)
    edges=(cv2.Canny(gray,24,72)>0)&clean
    observed_count = int(np.count_nonzero(edges))
    if observed_count > MAX_OBSERVED_EDGE_PIXELS:
        return np.zeros(mask.shape,bool), {
            'proposals':0, 'accepted_segments':0, 'protected_pixels':0,
            'observed_edge_pixels':observed_count, 'budget_exceeded':True}
    my,mx=np.where(mask)
    gap=min(128,max(int(mx.max()-mx.min()+1),int(my.max()-my.min()+1)))
    lines=cv2.HoughLinesP(edges.astype(np.uint8)*255,1,np.pi/360,12,
                          minLineLength=16,maxLineGap=gap)
    support=np.zeros(mask.shape,bool)
    if lines is None:
        return support,{'proposals':0,'accepted_segments':0,'protected_pixels':0,
                        'observed_edge_pixels':observed_count,'budget_exceeded':False}
    lines=lines[:,0].astype(np.float64)
    lengths=np.sum((lines[:,2:]-lines[:,:2])**2,axis=1)
    lines=lines[np.argsort(-lengths)[:MAX_LINE_PROPOSALS]]
    ey,ex=np.where(edges)
    gradient=np.column_stack((gx[ey,ex],gy[ey,ex])).astype(np.float64)
    gradient_norm=np.maximum(np.linalg.norm(gradient,axis=1),1e-6)
    observed=np.column_stack((ex,ey)).astype(np.float64)
    missing=np.column_stack((mx,my)).astype(np.float64)
    accepted=0
    for x0,y0,x1,y1 in lines:
        start=np.array([x0,y0]);tangent=np.array([x1-x0,y1-y0]);tangent/=np.linalg.norm(tangent)
        normal=np.array([-tangent[1],tangent[0]])
        distance=(observed-start)@normal
        orientation=np.abs(gradient@normal)/gradient_norm
        evidence=(np.abs(distance)<=1.5)&(orientation>=.85)
        if np.count_nonzero(evidence)<16:
            continue
        target_distance=(missing-start)@normal
        candidate=np.abs(target_distance)<=2.5
        if not np.any(candidate):
            continue
        positions=(missing[candidate]-start)@tangent
        lo,hi=float(positions.min()),float(positions.max())
        observed_position=(observed[evidence]-start)@tangent
        # Support must be close enough to delimit this actual occluded span,
        # not two unrelated collinear details far away in the bounded context.
        left=(observed_position<lo-1)&(observed_position>=lo-32)
        right=(observed_position>hi+1)&(observed_position<=hi+32)
        if np.count_nonzero(left)<8 or np.count_nonzero(right)<8:
            continue
        support[my[candidate],mx[candidate]]=True
        accepted+=1
    return support,{'proposals':len(lines),'accepted_segments':accepted,
                    'protected_pixels':int(np.count_nonzero(support)),
                    'observed_edge_pixels':observed_count,'budget_exceeded':False,
                    'rule':'observed clean gradients on both ends, at least 8 oriented samples per side'}


def refine(rgb, mask, *, initial, source_exclusion=None, quality='balanced'):
    """Return a uint8 RGB copy and bounded classical-refinement diagnostics.

    The write mask and excluded-source mask are distinct. Every complete donor
    comes from the unmodified input, wholly outside source exclusions and a two
    pixel guard. Excluded original pixels never become matching observations or
    donor pixels. Original values outside the write mask remain fixed Poisson
    boundary conditions, including source-excluded boundary pixels.

    Matching uses a harmonic low-frequency guide solved from original boundary
    colours, rather than trusting potentially miscopied initial texture. The
    initial image seeds that solve and supplies a weak matching continuation only
    where it agrees with nearby original background variation; never a donor.
    A 19-pixel overlapping donor field first searches nearby intact sources and
    then the same bounded context when nearby sources do not exist. A block with insufficient
    independent matching support inherits a supported neighbouring field; it
    does not lower the matching threshold. Each required pixel and gradient
    edge receives weighted overlapping donor values. Visible lines supported
    by clean oriented gradients at both ends of an occluded span protect a
    plausible initial edge continuation from inappropriate harmonic flattening.
    Missing intact sources, incomplete coverage, budget exhaustion or solver
    failure raise ValueError; no partial result or alternate repair is returned.
    """
    started = time.monotonic()
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8
            or rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 3):
        raise ValueError('consensus expects a nonempty H x W x 3 uint8 image, at least 3 x 3')
    if (not isinstance(initial, np.ndarray) or initial.dtype != np.uint8
            or initial.shape != rgb.shape):
        raise ValueError('consensus initial must be uint8 RGB with the original image shape')
    if (not isinstance(mask, np.ndarray) or mask.dtype != np.bool_
            or mask.shape != rgb.shape[:2]):
        raise ValueError('consensus mask must be a boolean array matching the image')
    if source_exclusion is not None and (
            not isinstance(source_exclusion, np.ndarray)
            or source_exclusion.dtype != np.bool_
            or source_exclusion.shape != mask.shape):
        raise ValueError('consensus source_exclusion must be a matching boolean array')
    if not isinstance(quality, str) or quality not in {'fast', 'balanced', 'high'}:
        raise ValueError('consensus quality must be fast, balanced, or high')
    count = int(np.count_nonzero(mask))
    if count > MAX_MASK_PIXELS:
        raise ValueError('consensus mask exceeds its 40000-pixel budget')
    if not count:
        return rgb.copy(), {
            'method': 'structure_guided_original_donor_consensus', 'processed_mask_pixels': 0,
            'votes': 0, 'needs_review': False, 'warnings': [],
            'elapsed_seconds': round(time.monotonic() - started, 4),
        }
    ys, xs = np.where(mask)
    r, p = PATCH_RADIUS, 2 * PATCH_RADIUS + 1
    margin = SEARCH_RADIUS + p
    y0 = max(0, int(ys.min()) - margin)
    y1 = min(mask.shape[0], int(ys.max()) + margin + 1)
    x0 = max(0, int(xs.min()) - margin)
    x1 = min(mask.shape[1], int(xs.max()) + margin + 1)
    h, w = y1-y0, x1-x0
    if (h + 2*r) * (w + 2*r) > MAX_CONTEXT_PIXELS:
        raise ValueError('consensus padded context exceeds its one-million-pixel budget')
    localmask = mask[y0:y1, x0:x1]
    excluded = localmask.copy()
    if source_exclusion is not None:
        excluded |= source_exclusion[y0:y1, x0:x1]
    local = rgb[y0:y1, x0:x1].astype(np.float32)
    # An initial patch fill can already contain an incorrectly borrowed object.
    # Blindly reusing its texture reinforces that mistake. The harmonic
    # colour guide depends on ORIGINAL fixed boundaries; initial pixels only
    # seed this solve. A separate consistency check below controls which initial
    # continuations may still assist matching. Neither is an output donor.
    scaffold, guide_solver = _solve(
        local, localmask,
        np.zeros((h, w-1, 3), np.float32),
        np.zeros((h-1, w, 3), np.float32),
        initial[y0:y1, x0:x1].astype(np.float32), screen_weight=0.0)
    scaffold = scaffold.astype(np.float32)
    # Keep an initial continuation only when it agrees with the background
    # range supported by nearby clean ORIGINAL pixels. This preserves a good
    # edge/texture continuation but removes conspicuous foreign-object copies.
    # These are still weak matching estimates; neither estimate is a donor.
    clean_weight = (~excluded).astype(np.float32)
    clean_mass = cv2.boxFilter(clean_weight, -1, (p,p), normalize=False,
                              borderType=cv2.BORDER_CONSTANT)
    original_mean = cv2.boxFilter(local*clean_weight[...,None], -1, (p,p),
                                  normalize=False, borderType=cv2.BORDER_CONSTANT)
    original_mean /= np.maximum(clean_mass[...,None], 1.0)
    original_second = cv2.boxFilter(local*local*clean_weight[...,None], -1, (p,p),
                                    normalize=False, borderType=cv2.BORDER_CONSTANT)
    original_second /= np.maximum(clean_mass[...,None], 1.0)
    original_sigma = np.sqrt(np.maximum(np.mean(original_second-original_mean*original_mean,
                                                axis=2), 0.0))
    initial_local = initial[y0:y1,x0:x1].astype(np.float32)
    disagreement = np.max(np.abs(initial_local-scaffold), axis=2)
    supported_range = np.maximum(12.0, 2.5*original_sigma)
    inconsistent = localmask & ((clean_mass < 12.0) | (disagreement > supported_range))
    inconsistent = cv2.dilate(inconsistent.astype(np.uint8), np.ones((5,5),np.uint8)) > 0
    observed_structure, structure_diag = _observed_line_support(local, localmask, excluded)
    trusted_initial = localmask & (~inconsistent | observed_structure)
    scaffold[trusted_initial] = initial_local[trusted_initial]
    src = np.pad(local, ((r,r),(r,r),(0,0)), mode='reflect')
    query = np.pad(scaffold, ((r,r),(r,r),(0,0)), mode='reflect')
    hole = np.pad(localmask, r, mode='constant')
    # Reflected query geometry never authorizes synthetic donor pixels beyond
    # the actual image edge. A complete source patch must be in real input.
    blocked = np.pad(excluded, r, mode='constant', constant_values=True)
    guarded = cv2.dilate(blocked.astype(np.uint8),
                          np.ones((2*SOURCE_GUARD+1, 2*SOURCE_GUARD+1), np.uint8)) != 0
    allowed = cv2.boxFilter((~guarded).astype(np.float32), -1, (p,p),
                           normalize=False, borderType=cv2.BORDER_CONSTANT) > p*p-.1
    allowed[:r] = allowed[-r:] = False
    allowed[:,:r] = allowed[:,-r:] = False
    source_y, source_x = np.where(allowed)
    if not len(source_y):
        raise ValueError('consensus found no intact source patch in the bounded context')
    allowed_positions = np.column_stack((source_y, source_x)).astype(np.int64)
    reference_weight = (~blocked).astype(np.float32) + hole.astype(np.float32)*.08
    yy, xx = np.mgrid[-r:r+1, -r:r+1]
    gaussian = np.exp(-(yy*yy+xx*xx)/(2*(r*.85)**2)).astype(np.float32)
    fy, fx = np.where(hole)
    centers = []
    for ty in range(max(r, int(fy.min())-r), min(src.shape[0]-r, int(fy.max())+r+1), CENTER_STRIDE):
        for tx in range(max(r, int(fx.min())-r), min(src.shape[1]-r, int(fx.max())+r+1), CENTER_STRIDE):
            if hole[ty-r:ty+r+1, tx-r:tx+r+1].any():
                centers.append((ty,tx))
                if len(centers) > MAX_VOTE_CENTERS:
                    raise ValueError('consensus exceeds its 2000-vote-center budget')
    weights_list, donor_choices = [], []
    unsupported, expanded, match_locations = [], 0, 0
    min_source_y, max_source_y = int(source_y.min()), int(source_y.max())+1
    min_source_x, max_source_x = int(source_x.min()), int(source_x.max())+1
    for index, (ty,tx) in enumerate(centers):
        target = query[ty-r:ty+r+1,tx-r:tx+r+1]
        weights = reference_weight[ty-r:ty+r+1,tx-r:tx+r+1]*gaussian
        weights_list.append(weights)
        mass = float(weights.sum())
        if mass < 3.0:
            donor_choices.append(None); unsupported.append(index)
            continue
        ay,by = max(r,ty-SEARCH_RADIUS), min(src.shape[0]-r,ty+SEARCH_RADIUS+1)
        ax,bx = max(r,tx-SEARCH_RADIUS), min(src.shape[1]-r,tx+SEARCH_RADIUS+1)
        if not allowed[ay:by,ax:bx].any():
            # All candidates still lie in the already budgeted context; no
            # additional source area, weaker mask, or synthetic patch is used.
            ay,by,ax,bx = min_source_y,max_source_y,min_source_x,max_source_x
            expanded += 1
        match_locations += (by-ay)*(bx-ax)
        if match_locations > MAX_MATCH_LOCATIONS:
            raise ValueError('consensus exceeds its 128-million matching-location budget')
        source = src[ay-r:by+r,ax-r:bx+r]
        comparison = np.repeat(np.sqrt(weights)[...,None],3,axis=2)
        ssd = cv2.matchTemplate(source,target,cv2.TM_SQDIFF,mask=comparison)/mass
        mean = np.sum(target*weights[...,None],axis=(0,1))/mass
        source_mean = np.stack([cv2.matchTemplate(source[...,c],weights,cv2.TM_CCORR)/mass
                                for c in range(3)],axis=-1)
        raw_offset = mean-source_mean
        offset = np.clip(raw_offset,-20.,20.)
        error = np.maximum(ssd-2*np.sum(offset*raw_offset,axis=-1)+np.sum(offset*offset,axis=-1),0)
        cy,cx = np.mgrid[ay:by,ax:bx]
        cost = error+.10*np.sum(offset*offset,axis=-1)+.0008*((cy-ty)**2+(cx-tx)**2)
        finite = allowed[ay:by,ax:bx] & np.isfinite(cost)
        if not finite.any():
            raise ValueError('consensus produced no finite intact donor candidate')
        cost = np.where(finite,cost,np.inf)
        iy,ix = np.unravel_index(int(np.argmin(cost)),cost.shape)
        donor_choices.append(_candidate(src,query,weights,(ty,tx),(ay+iy,ax+ix)))
    supported = [i for i,item in enumerate(donor_choices) if item is not None]
    if not supported:
        raise ValueError('consensus has no independently supported donor field seed')
    center_array = np.asarray(centers,np.int64)
    propagated = 0
    # Inherit an intact source displacement from the closest supported target
    # block. Project only SOURCE coordinates when that displacement is blocked.
    for index in unsupported:
        delta = center_array[supported]-center_array[index]
        neighbor_index = supported[int(np.argmin(np.sum(delta*delta,axis=1)))]
        inherited = donor_choices[neighbor_index]
        proposed = np.asarray(inherited['source']) + center_array[index]-center_array[neighbor_index]
        sy,sx = map(int,proposed)
        if not (0<=sy<allowed.shape[0] and 0<=sx<allowed.shape[1] and allowed[sy,sx]):
            distance = np.sum((allowed_positions-proposed)**2,axis=1)
            sy,sx = map(int,allowed_positions[int(np.argmin(distance))])
        candidate = _candidate(src,query,weights_list[index],centers[index],(sy,sx),
                               inherited_offset=inherited['offset'])
        donor_choices[index]=candidate; propagated += 1
    low_support=set(unsupported)
    color=np.zeros_like(src)
    color_priority=np.zeros(src.shape[:2],np.float32)
    gx=np.zeros((src.shape[0],src.shape[1]-1,3),np.float32)
    gy=np.zeros((src.shape[0]-1,src.shape[1],3),np.float32)
    x_priority,y_priority=np.zeros(gx.shape[:2],np.float32),np.zeros(gy.shape[:2],np.float32)
    scores=[]
    for index,((ty,tx),candidate) in enumerate(zip(centers,donor_choices)):
        sy,sx=candidate['source']
        if not allowed[sy,sx]:
            raise ValueError('consensus donor escaped its intact source constraints')
        donor=src[sy-r:sy+r+1,sx-r:sx+r+1]
        rms=float(np.sqrt(candidate['error']/3.))
        if index not in low_support:
            scores.append(rms)
        # Internal gradients are measured independently in each intact donor,
        # then combined with overlapping support. Pasted-image seams never
        # become gradient evidence. Averaging can still soften fine texture.
        vote=gaussian/(1.+rms/8.)
        if index in low_support:
            vote=vote*.6
        py,px=slice(ty-r,ty+r+1),slice(tx-r,tx+r+1)
        color[py,px]+=(donor+candidate['offset'])*vote[...,None]
        color_priority[py,px]+=vote
        vx=(vote[:,:-1]+vote[:,1:])*.5;vy=(vote[:-1]+vote[1:])*.5
        sx_slice=(slice(ty-r,ty+r+1),slice(tx-r,tx+r))
        sy_slice=(slice(ty-r,ty+r),slice(tx-r,tx+r+1))
        gx[sx_slice]+=(donor[:,1:]-donor[:,:-1])*vx[...,None]
        gy[sy_slice]+=(donor[1:]-donor[:-1])*vy[...,None]
        x_priority[sx_slice]+=vx
        y_priority[sy_slice]+=vy
    if np.any(hole & (color_priority<=0)):
        raise ValueError('consensus has incomplete color-vote coverage')
    required_x=localmask[:,:-1]|localmask[:,1:]
    required_y=localmask[:-1]|localmask[1:]
    if (np.any(required_x & (x_priority[r:r+h,r:r+w-1]<=0)) or
            np.any(required_y & (y_priority[r:r+h-1,r:r+w]<=0))):
        raise ValueError('consensus has incomplete guidance for a required gradient edge')
    color /= np.maximum(color_priority[...,None],1e-8)
    gx /= np.maximum(x_priority[...,None],1e-8)
    gy /= np.maximum(y_priority[...,None],1e-8)
    prior=color[r:r+h,r:r+w]
    local_gx,local_gy=gx[r:r+h,r:r+w-1],gy[r:r+h-1,r:r+w]
    if (not np.all(np.isfinite(prior[localmask])) or not np.all(np.isfinite(local_gx[required_x]))
            or not np.all(np.isfinite(local_gy[required_y]))):
        raise ValueError('consensus accumulated non-finite guidance')
    solved,solver=_solve(local,localmask,local_gx,local_gy,prior)
    values=solved[localmask]
    if not np.all(np.isfinite(values)):
        raise ValueError('consensus produced non-finite output')
    clipped=int(np.count_nonzero(np.any((values<0)|(values>255),axis=1)))
    output=rgb.copy()
    output[y0:y1,x0:x1][localmask]=np.rint(values).clip(0,255).astype(np.uint8)
    warnings=[
        'Original donor estimates may still select inappropriate texture, and overlapping consensus can soften fine detail.',
        'The input selection must cover the watermark; unselected remnants remain unchanged.',
        'Hidden original content is unknown; inspect the result before accepting it.',
    ]
    if float(np.percentile(scores,95))>20.:
        warnings.append('Some donor matches are weak; copied texture may be inappropriate.')
    fixed_boundary=cv2.dilate(localmask.astype(np.uint8),
                              np.asarray([[0,1,0],[1,1,1],[0,1,0]],np.uint8))>0
    excluded_boundary_count=int(np.count_nonzero(fixed_boundary & excluded & ~localmask))
    if excluded_boundary_count:
        warnings.append('Source-excluded pixels preserved outside the write mask constrain the Poisson boundary; protected watermark colors can influence adjacent repairs.')
    if structure_diag.get('budget_exceeded'):
        warnings.append('Visible-line protection abstained because its 50000-edge-pixel evidence budget was exceeded.')
    if clipped:
        warnings.append('Some refined colors were clipped to the valid 8-bit range.')
    return output,{
        'method':'structure_guided_original_donor_consensus',
        'restoration_kind':'optional_structure_guided_gradient_refinement',
        'initial_quality_hint':quality,
        'refinement_profile':'19px_patch_6px_stride_structure_guided_bounded_context',
        'processed_mask_pixels':count,
        'patch_radius':r,'search_radius':SEARCH_RADIUS,'center_stride':CENTER_STRIDE,
        'source_guard_radius':SOURCE_GUARD,
        'source_policy':'intact original patches only, inside real image; independent internal gradients',
        'source_excluded_pixels_in_context':int(np.count_nonzero(excluded)),
        'excluded_fixed_boundary_pixels':excluded_boundary_count,
        'intact_source_centers':len(source_y),
        'vote_centers':len(centers),'votes':len(donor_choices),'skipped_centers':0,
        'independently_scored_centers':len(scores),
        'matching_locations_evaluated':match_locations,
        'bounded_context_search_centers':expanded,
        'low_support_propagated_centers':propagated,
        'gradient_aggregation':'weighted overlapping independent original-donor gradients',
        'color_aggregation':'weighted overlapping original-donor colours',
        'covered_write_pixels':int(np.count_nonzero(localmask & (color_priority[r:r+h,r:r+w]>0))),
        'covered_required_x_edges':int(np.count_nonzero(required_x)),
        'covered_required_y_edges':int(np.count_nonzero(required_y)),
        'donor_rms_median':float(np.median(scores)),
        'donor_rms_p95':float(np.percentile(scores,95)),
        'roi_xyxy_exclusive':[x0,y0,x1,y1],
        'clipped_pixels':clipped,'solver':solver,
        'matching_guide':'harmonic colours plus locally consistent or bilaterally observed-edge-supported weak initial continuation; never a donor',
        'initial_matching_pixels_retained':int(np.count_nonzero(trusted_initial)),
        'initial_matching_pixels_replaced':int(np.count_nonzero(localmask & ~trusted_initial)),
        'initial_consistency_rule':'at least 12 clean samples; max RGB deviation <= max(12, 2.5*local original RGB sigma), with 2px rejection guard',
        'guide_solver':guide_solver,
        'observed_structure':structure_diag,
        'line_proposal_budget':MAX_LINE_PROPOSALS,
        'observed_edge_pixel_budget':MAX_OBSERVED_EDGE_PIXELS,
        'initial_role':'weak matching continuation with local background consistency or observed bilateral line support',
        'needs_review':True,'warnings':warnings,
        'outside_write_mask_changed_pixels':0,
        'elapsed_seconds':round(time.monotonic()-started,4),
        'accuracy_against_original':None,
    }
