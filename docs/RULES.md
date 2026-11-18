# Classification rules

Every change gets one severity (`breaking`, `warning` or `safe`), a short rule
id and a one-sentence reason. A change marked `x-internal` is downgraded from
breaking to warning. `x-sunset` dates are compared with the `today` passed to
the classifier, never with the machine clock.

| Rule id | Kind | Where | Severity | Why |
| --- | --- | --- | --- | --- |
| `endpoint-removed` | `endpoint_removed` | endpoint | breaking | Clients still call it. |
| `endpoint-added` | `endpoint_added` | endpoint | safe | Nothing existing changes. |
| `endpoint-deprecated-changed` | `deprecated_changed` | endpoint | warning | A heads-up, not a contract break. |
| `param-added` | `param_added` | request param | breaking if required, safe if optional | Clients may not send a new required value. |
| `param-removed` | `param_removed` | request param | warning | Clients may still send it. |
| `param-required-changed` | `required_changed` | request param | false -> true breaking, true -> false safe | A new required value breaks senders. |
| `param-type-changed` | `type_changed` | request param | breaking | Clients send the old type. |
| `param-enum-values-removed` | `enum_values_removed` | request param | breaking | Clients may send a removed value. |
| `param-enum-values-added` | `enum_values_added` | request param | safe | Extra accepted values do not hurt senders. |
| `request-body-added` | `request_body_added` | request body | breaking if required, safe if optional | Clients may not send a new required body. |
| `request-body-removed` | `request_body_removed` | request body | warning | Clients may still send a body. |
| `request-body-required-changed` | `required_changed` | request body | false -> true breaking, true -> false safe | A new required body breaks senders. |
| `request-field-added` | `field_added` | request field | breaking if required, safe if optional | Clients may not send a new required field. |
| `request-field-removed` | `field_removed` | request field | warning | Clients may still send it. |
| `request-field-required-changed` | `required_changed` | request field | false -> true breaking, true -> false safe | A new required field breaks senders. |
| `request-field-type-changed` | `type_changed` | request field | breaking | Clients send the old type. |
| `request-field-format-changed` | `format_changed` | request field | warning | The value format may need attention. |
| `request-field-enum-values-removed` | `enum_values_removed` | request field | breaking | Clients may send a removed value. |
| `request-field-enum-values-added` | `enum_values_added` | request field | safe | Extra accepted values do not hurt senders. |
| `request-field-deprecated-changed` | `deprecated_changed` | request field | warning | A heads-up with the sunset date. |
| `request-field-sunset-changed` | `sunset_changed` | request field | warning | A heads-up with the sunset date. |
| `response-field-added` | `field_added` | response field | safe | Readers can ignore extra fields. |
| `response-field-removed` | `field_removed` | response field | breaking, or warning after its `x-sunset` date | Readers lose data. Removing after sunset is expected; removing early is breaking. |
| `response-field-required-changed` | `required_changed` | response field | true -> false breaking, false -> true safe | Readers may now get no value. |
| `response-field-type-changed` | `type_changed` | response field | breaking | Readers parse the old type. |
| `response-field-format-changed` | `format_changed` | response field | warning | The value format may need attention. |
| `response-field-enum-values-added` | `enum_values_added` | response field | warning | Strict clients may fail on an unknown value. |
| `response-field-enum-values-removed` | `enum_values_removed` | response field | safe | Readers only lose values they may not see. |
| `response-field-deprecated-changed` | `deprecated_changed` | response field | warning | A heads-up with the sunset date. |
| `response-field-sunset-changed` | `sunset_changed` | response field | warning | A heads-up with the sunset date. |
| `response-added` | `response_added` | response | warning | Clients may need to handle a new status code. |
| `response-removed` | `response_removed` | response | breaking for 2xx, warning otherwise | Clients that read a success response lose it. |
| `semver-minor-breaking` | version bump | whole spec | warning | Breaking changes were published without a major version bump. |

## Notes

- `x-internal` changes never block a publish: a breaking rule becomes a warning,
  and the reason says only the owning team uses it.
- `response-field-removed` checks the old field's `x-sunset`. The comparison is
  `sunset <= today`. A missing sunset means breaking.
- Version checking only runs when there is at least one breaking change. It
  compares the major part of the two semver labels.
- An unknown change kind is reported as rule `unclassified-change` with warning
  severity so it is not silently dropped.
