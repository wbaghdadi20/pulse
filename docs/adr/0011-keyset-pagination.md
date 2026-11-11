# 11. Keyset pagination next to limit/offset

- Status: accepted
- Date: 2026-11-11 (D2 Extra)

## Context

`GET /monitors/` is paginated with `?limit=&offset=` and returns a total count.
That is DRF's `LimitOffsetPagination`. It has two costs that grow with the table,
measured on the 500,000-row `checks` table since `monitors` is small:

| Query                                       | Rows read | Time    |
| ------------------------------------------- | --------- | ------- |
| first page, `OFFSET 20 LIMIT 20`            | 40        | 0.08 ms |
| last page, `OFFSET 499000 LIMIT 20`         | 499,020   | 67.9 ms |
| last page by key, `WHERE id > 499000 LIMIT 20` | 20     | 0.07 ms |
| `COUNT(*)` for the total                    | 500,000   | 26.7 ms |

`OFFSET n` is not a jump: Postgres walks the index over `n` rows and throws
them away. The count is a second full pass on every request.

Offset pages also move when rows change. Delete a row the client has already
seen and every later row shifts up by one, so the next page skips one.

## Options

1. **Offset only.** Fine while tables are small, which `monitors` is.
2. **DRF's `CursorPagination`.** Built in, both directions, any stable ordering.
   Its cursor is an opaque encoded value, and it needs its own switch since the
   first page carries no cursor.
3. **`?after=<last id>`, written by hand** (chosen), as the ticket describes.
4. **Replace offset with keyset.** Breaks the D2 contract (`count`, `offset`).

## Decision

`GET /monitors/?after=<id>&limit=20` returns rows with `id > after`, ordered by
id, as `{"next": <url or null>, "results": [...]}`. `after=0` starts from the
beginning. Without `after` the endpoint behaves exactly as before.

`core.pagination.AfterIdPagination` fetches `limit + 1` rows: the extra one says
whether there is a next page, so there is no count query. `limit` is read by the
same code as offset pagination (default 20, at most 100).

## Consequences

- One query per page, and its cost does not depend on how deep the page is.
- Stable while rows are inserted or deleted: a page is defined by a value, not
  a position. A test deletes a row between two pages and shows offset skipping
  one where keyset does not.
- No total and no "page 37 of 80". A client can only walk forward.
- Tied to ordering by `id`. Ordering by name would need the cursor to carry
  `(name, id)`; at that point option 2 is the better tool.
- Two pagination styles on one endpoint to document and test.
- Not a DRF pagination class: it requires a queryset of model instances, which
  is narrower than `BasePagination`'s contract. The view picks between the two.
