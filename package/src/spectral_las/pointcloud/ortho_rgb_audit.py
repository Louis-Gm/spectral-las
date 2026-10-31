from __future__ import annotations
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid
from .ortho_rgb_batch import scan_inputs

COLUMNS=("filename","full_path","tile_id","input_type","acquisition_id","reason",
         "expected_companion_type","expected_companion_prefix","expected_companion_acquisition")

def _csv(path,rows):
    with path.open("x",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader()
        for row in rows: w.writerow({k:row.get(k,"") for k in COLUMNS})
        f.flush();os.fsync(f.fileno())

def audit_tile_inputs(working_directory,output_subfolder="rgb",prefix="1km",tile_id_digits=11,
                      lidar_acquisition=None,ortho_acquisition=None,write_reports=True):
    root,output,index,issues,ignored=scan_inputs(working_directory,output_subfolder,prefix,tile_id_digits,lidar_acquisition,ortho_acquisition)
    unmatched=[];ambiguous=[];missing=[];pairs=0
    for tile,group in sorted(index.items()):
        is_ambiguous=len(group["lidar"])>1 or len(group["ortho"])>1
        absent="lidar" if not group["lidar"] else "ortho" if not group["ortho"] else None
        if absent:
            missing.append(tile)
            for rows in group.values():
                for row in rows:
                    unmatched.append(dict(row,reason=f"missing_matching_{absent}",expected_companion_type=absent,
                                          expected_companion_prefix=prefix+tile,
                                          expected_companion_acquisition=(lidar_acquisition if absent=="lidar" else ortho_acquisition) or ""))
        if is_ambiguous:
            ambiguous.append(tile)
            for rows in group.values():
                for row in rows: issues.append(dict(row,reason="ambiguous_tile_multiple_candidates"))
        if absent is None and not is_ambiguous: pairs+=1
    ident=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    summary=dict(schema_version=1,audit_id=ident,working_directory=str(root),matched_pairs=pairs,
                 unmatched_files=len(unmatched),unmatched_lidar_files=sum(r["input_type"]=="lidar" for r in unmatched),
                 unmatched_ortho_files=sum(r["input_type"]=="ortho" for r in unmatched),unmatched_tile_ids=missing,
                 ambiguous_tile_ids=ambiguous,malformed_filenames=sum(r["reason"]=="malformed_filename" for r in issues),
                 excluded_by_acquisition_filter=sum(r["reason"]=="excluded_by_acquisition_filter" for r in issues),
                 ignored_other_files=ignored,lidar_acquisition_filter=lidar_acquisition,
                 ortho_acquisition_filter=ortho_acquisition,unmatched_examples=unmatched[:10],reports={})
    if write_reports:
        directory=output/"logs";directory.mkdir(parents=True,exist_ok=True)
        a=directory/f"unmatched-files-{ident}.csv";b=directory/f"input-issues-{ident}.csv";c=directory/f"input-audit-{ident}.json"
        summary["reports"]=dict(unmatched_csv=str(a),issues_csv=str(b),summary_json=str(c))
        _csv(a,unmatched);_csv(b,issues)
        with c.open("x",encoding="utf-8") as f:
            json.dump(summary,f,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
    return summary
