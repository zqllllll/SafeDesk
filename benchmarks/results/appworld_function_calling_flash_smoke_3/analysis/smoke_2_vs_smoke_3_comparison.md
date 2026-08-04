# smoke_2 vs smoke_3 Comparison

| Metric | smoke_2 | smoke_3 | Change |
| --- | ---: | ---: | ---: |
| success | False | False |  |
| passed_tests | 1 | 1 |  |
| num_turns | 6 | 20 |  |
| num_tool_calls_executed | 41 | 20 | -51.22% |
| duplicate_tool_calls | not recorded | 0 |  |
| input_tokens | 95270 | 74644 | -21.65% |
| output_tokens | 2211 | 1626 | -26.46% |
| total_tokens | 97481 | 76270 | -21.76% |
| duration_seconds | 20.0285 | 27.6 |  |
| completion_step | turn 6 | None |  |
| tool_schema_count | 101 | 11 | -89.11% |

## Notes

- smoke_3 reduced exposed schemas from 101 to 11 and executed calls from 41 to 20.
- Provider still emitted multi-tool responses despite parallel_tool_calls=false; adapter suppressed 7 calls and executed no parallel batch.
- smoke_3 did not complete within 20 turns because strict one-tool execution is too slow for a paginated plus many-song-detail task.
- Next optimization should add state summarization or a benchmark-side safe aggregate/read helper, not re-enable uncontrolled parallel batches.
