"""Offline size-only study. Does not alter Reviewer defaults or send messages."""
import argparse
import json
import sys
from pathlib import Path


def group_read_operations(rows):
    batches = {}
    for row in rows:
        if row['type'] == 'read' and row['status'] == 'ok':
            batches.setdefault((row['call'], row['step']), []).append(row)
    return batches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewer-source', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.reviewer_source / 'src'))
    from webgpt_reviewer_mcp.batching import encoded_size
    from webgpt_reviewer_mcp.workspace import Workspace, read_file

    state = json.loads(args.state.read_text(encoding='utf-8'))
    workspace = Workspace(state['workspace_id'], args.workspace)
    batches = {}
    for key, rows in group_read_operations(state['operation_trace']).items():
        for row in rows:
            result = read_file(workspace, row['path'], row['start_line'], row['end_line'])
            result['line_truncated'] = row['line_truncated']
            item = {'id': 'x' * 32, 'index': row['index'], 'status': 'ok',
                    'gateway_executed': True, 'result': result}
            batches.setdefault(key, []).append(item)

    def size(items):
        return encoded_size({'gateway_batch': items})

    results = []
    for kib in (16, 24, 32):
        limit = kib * 1024
        entry = {'cap_kib': kib, 'operations_executed_in_original': state['operation_count'],
                 'known_batches': [], 'data_silently_truncated': False}
        for (call, step), items in batches.items():
            if len(items) != 4:
                continue  # Batch 2's 3 search items are not reconstructed.
            # Exact admission policy uses 1KiB reserve for each remaining item.
            admitted = 0
            for i, item in enumerate(items):
                trial = [*items[:i], item, *[
                    {'id': 'x' * 32, 'index': j, 'status': 'not_executed'}
                    for j in range(i + 1, 4)]]
                if size(trial) + 1024 * (3-i) > limit:
                    break
                admitted += 1
            groups, current, oversized = [], [], []
            for item in items:
                if size([item]) > limit:
                    oversized.append({'index': item['index'], 'single_item_bytes': size([item])})
                    continue
                if current and size([*current, item]) > limit:
                    groups.append(current)
                    current = []
                current.append(item)
            if current:
                groups.append(current)
            entry['known_batches'].append({
                'call': call, 'step': step, 'reconstructed_envelope_bytes_upper_bound': size(items),
                'direct_cap_accepts_batch': admitted == 4,
                'direct_cap_local_operations_before_terminal_error': min(admitted + 1, 4),
                'direct_cap_remote_evidence_delivery': 'all' if admitted == 4 else 'none',
                'hypothetical_presplit_groups': len(groups),
                'hypothetical_extra_evidence_round_trips': None if oversized else len(groups)-1,
                'oversized_individual_items': oversized,
                'truncation': 'none; oversize requires narrower requested line ranges, not silent clipping',
            })
        results.append(entry)
    print(json.dumps({'study': results, 'provenance': {
        'read_operations_reconstructed': sum(map(len, batches.values())),
        'unknown_search_operations': sum(row['type'] == 'search' for row in state['operation_trace']),
        'trace_truncated': state.get('trace_truncated', False),
        'original_id_lengths_unknown': True,
        'ids_use_schema_maximum_length_for_conservative_estimate': True,
        'bridge_user_wrapper_not_in_reviewer_cap': True,
        'reviewer_default_64_kib_unchanged': True,
        'limits_apply_to_output_not_only_insert_payload': True,
        'whole_review_round_trips_not_measured_or_predictable': True,
        'presplitting_is_counterfactual_not_implemented': True,
        'presplit_estimates_exclude_reserve_policy_and_model_behavior': True,
        'direct_cap_error_is_terminal_not_model_recoverable': True,
        'no_new_model_calls_or_operations': True,
    }}, indent=2))


if __name__ == '__main__':
    main()
