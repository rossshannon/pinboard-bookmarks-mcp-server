/**
 * Ported unchanged from the &Open MCP server.
 *
 * Build-time `zod/v4` → `zod/mini` adapter for the card bundle: `build-cards.ts`
 * aliases Vite's `zod/v4` resolution here, so the SDK code that validates the
 * host's postMessage frames (`sdk/types.js`, `ext-apps`) builds against mini's
 * lighter runtime instead of classic zod's. Mini shares the same validation core
 * but exposes it functionally, so the Proxy below re-presents the chainable API
 * the SDK is written against (`.optional()`, `.extend()`, `.min()`, …) over it.
 *
 * Vite bundles this into the card document, so it is shipped browser code rather
 * than tooling — hence its home under `src/`. The alias is scoped to
 * the card build; server code keeps the real `zod`.
 *
 * `zod-shim-parity.test.ts` beside it is the guard: it runs the SDK's schemas through both
 * this adapter and real zod and asserts identical accept/reject, so a zod or SDK
 * change the method map below does not cover breaks the build rather than
 * shipping a card whose frame validation diverges from the host's.
 */
import * as m from 'zod/mini'


/** Any non-null object: arrays included, unlike the cards' own `isRecord`, since a zod schema or shape is only ever looked for by key. */
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

type Method = (schema: MiniType, arguments_: readonly unknown[]) => MiniType
type MiniObject = m.ZodMiniObject
type MiniType = m.ZodMiniType

/**
 * Types whose `.min()`/`.max()` mean length, not magnitude. Getting this wrong
 * is silent: a numeric check against an array compares an object to a number,
 * which is `NaN`, so every value fails rather than the bound being applied.
 */
const SIZED_TYPES = new Set(['array', 'file', 'map', 'set'])

const hasLengthSemantics = (schema: MiniType): boolean =>
  schema.def.type === 'string' || SIZED_TYPES.has(schema.def.type)

const isLiteral = (schema: MiniType): schema is m.ZodMiniLiteral => schema.def.type === 'literal'

const isLiteralList = (value: m.core.util.Literal | readonly m.core.util.Literal[]): value is readonly m.core.util.Literal[] => Array.isArray(value)

const isObject = (schema: MiniType): schema is MiniObject => schema.def.type === 'object'

const isSchema = (value: unknown): value is MiniType => isRecord(value) && '_zod' in value

/**
 * The narrowers below throw rather than degrade: a miss means the method map is
 * wrong about what the SDK passes, which is what the parity suite exists to catch.
 */
const objectSchema = (schema: MiniType): MiniObject => {
  if (!isObject(schema)) throw new TypeError(`zod shim: expected an object schema, got ${schema.def.type}`)
  return schema
}

const schemaArgument = (value: unknown): MiniType => {
  if (!isSchema(value)) throw new TypeError('zod shim: expected a schema argument')
  return value
}

const shapeArgument = (value: unknown): Record<string, MiniType> => {
  if (!isRecord(value)) throw new TypeError('zod shim: expected a shape argument')
  return Object.fromEntries(Object.entries(value).map(([key, member]) => [key, schemaArgument(member)]))
}

const maskArgument = (value: unknown): Record<string, true | undefined> => {
  if (!isRecord(value)) throw new TypeError('zod shim: expected a mask argument')
  // Falsy entries are dropped rather than coerced, as classic zod does. The key
  // stays present so zod's own unrecognised-key check on the mask still sees it.
  return Object.fromEntries(Object.entries(value).map(([key, selected]) => [key, selected === true ? true : undefined]))
}

const numberArgument = (value: unknown): number => {
  if (typeof value !== 'number') throw new TypeError('zod shim: expected a numeric argument')
  return value
}

const stringArgument = (value: unknown): string => {
  if (typeof value !== 'string') throw new TypeError('zod shim: expected a string argument')
  return value
}

const functionArgument = (value: unknown): ((input: unknown) => unknown) => {
  if (typeof value !== 'function') throw new TypeError('zod shim: expected a function argument')
  const called = (input: unknown): unknown => Reflect.apply(value, undefined, [input])
  return called
}

/**
 * Change an object schema's unknown-key policy in place. Rebuilding it through
 * the factories would drop any `.refine()` chained earlier — a divergence no
 * accept/reject comparison against the SDK's own schemas would reveal.
 */
const withCatchall = (schema: MiniType, catchall?: MiniType): MiniObject => {
  const target = objectSchema(schema)
  return target.clone({ ...target.def, catchall })
}

/**
 * Classic chainable methods mapped to `zod/mini`. `readonly` is an identity:
 * output-freezing that no accept/reject depends on.
 */
const METHODS: Readonly<Record<string, Method>> = {
  and: (schema, [other]) => m.intersection(schema, schemaArgument(other)),
  array: schema => m.array(schema),
  catch: (schema, [value]) => m.catch(schema, value),
  catchall: (schema, [inner]) => m.catchall(objectSchema(schema), schemaArgument(inner)),
  default: (schema, [value]) => m._default(schema, value),
  // The description reaches the host through `toJSONSchema` when a card
  // registers a tool. Cloned and registered as classic zod does it: routing
  // through `.check()` leaves a `def.checks` entry that makes `pick`/`merge`/
  // `partial` reject the schema as refinement-bearing.
  describe: (schema, [description]) => {
    const described = schema.clone()
    m.core.globalRegistry.add(described, { description: stringArgument(description) })
    return described
  },
  extend: (schema, [shape]) => m.extend(objectSchema(schema), shapeArgument(shape)),
  int: schema => schema.check(m.int()),
  loose: schema => withCatchall(schema, m.unknown()),
  max: (schema, [bound]) => schema.check(hasLengthSemantics(schema) ? m.maxLength(numberArgument(bound)) : m.lte(numberArgument(bound))),
  // Classic `merge` takes the incoming schema's policy and checks, not the receiver's.
  merge: (schema, [other]) => {
    const source = objectSchema(schemaArgument(other))
    return source.clone({ ...source.def, shape: { ...objectSchema(schema).shape, ...source.shape } })
  },
  min: (schema, [bound]) => schema.check(hasLengthSemantics(schema) ? m.minLength(numberArgument(bound)) : m.gte(numberArgument(bound))),
  nonnegative: schema => schema.check(m.nonnegative()),
  nullable: schema => m.nullable(schema),
  optional: schema => m.optional(schema),
  or: (schema, [other]) => m.union([schema, schemaArgument(other)]),
  partial: (schema, [mask]) => mask === undefined ? m.partial(objectSchema(schema)) : m.partial(objectSchema(schema), maskArgument(mask)),
  passthrough: schema => withCatchall(schema, m.unknown()),
  pick: (schema, [mask]) => m.pick(objectSchema(schema), maskArgument(mask)),
  readonly: schema => schema,
  refine: (schema, [check]) => schema.check(m.refine(functionArgument(check))),
  startsWith: (schema, [prefix]) => schema.check(m.startsWith(stringArgument(prefix))),
  strict: schema => withCatchall(schema, m.never()),
  strip: schema => withCatchall(schema),
  transform: (schema, [mapper]) => m.pipe(schema, m.transform(functionArgument(mapper))),
  url: schema => schema.check(m.url()),
}

const wrap = <T extends MiniType>(schema: T): T => {
  const proxy = new Proxy(schema, {
    get(target, property) {
      // Classic zod defines `ZodLiteral#value` and `zod/mini` does not; ext-apps
      // reads it to key its handler registry, so an `undefined` here collapses
      // that registry to a single entry.
      if (property === 'value' && isLiteral(target)) {
        if (target.def.values.length > 1) throw new TypeError('zod shim: literal has multiple values, read .values instead')
        return target.def.values[0]
      }

      const method = METHODS[property.toString()]
      if (method !== undefined) return (...arguments_: readonly unknown[]) => wrap(method(target, arguments_))

      const value: unknown = Reflect.get(target, property, target)
      if (typeof value !== 'function') return value
      // Re-bind to the target: `zod/mini`'s methods read internals off `this`,
      // which the proxy is not.
      return (...arguments_: readonly unknown[]): unknown => Reflect.apply(value, target, arguments_)
    },
  })

  // zod's metadata registry keys on object identity, so a Proxy never finds the
  // entry `.describe()` wrote against the schema it wraps — without this copy,
  // every card-registered tool's schema serialises description-free.
  const metadata = m.core.globalRegistry.get(schema)
  if (metadata !== undefined) m.core.globalRegistry.add(proxy, metadata)

  return proxy
}

const lift = <A extends readonly unknown[], R extends MiniType>(factory: (...arguments_: A) => R) =>
  (...arguments_: A): R => wrap(factory(...arguments_))

// `sdk/types.js` does `import * as z from 'zod/v4'` and `ext-apps` does
// `import { z } from 'zod/v4'`, so every factory must exist both as a named
// module export and on the aggregate `z` below.
export const any = lift(m.any)
export const array = lift(m.array)
export const boolean = lift(m.boolean)
export const custom = lift(m.custom)
export const discriminatedUnion = lift(m.discriminatedUnion)
const enum_ = lift(m.enum)
export { enum_ as enum }
export const intersection = lift(m.intersection)
export const iso = { datetime: lift(m.iso.datetime) }
const never_ = lift(m.never)
export { never_ as never }
const undefined_ = lift(m.undefined)
export { undefined_ as undefined }
// Classic `literal` takes a value or a list of them; `lift` would collapse the
// two overloads to the single-value one, so the list form is dispatched here.
export const literal = lift((value: m.core.util.Literal | readonly m.core.util.Literal[]) =>
  isLiteralList(value) ? m.literal([...value]) : m.literal(value))
export const looseObject = lift(m.looseObject)
const null_ = lift(m.null)
export { null_ as null }
export const number = lift(m.number)
export const object = lift(m.object)
export const optional = lift(m.optional)
export const preprocess = lift((mapper: (input: never) => unknown, schema: MiniType) => m.pipe(m.transform(mapper), schema))
export const record = lift(m.record)
export const strictObject = lift(m.strictObject)
export const string = lift(m.string)
export const union = lift(m.union)
export const unknown = lift(m.unknown)

/**
 * `ext-apps`'s `App` constructor calls `z.config({ jitless: true })` on every card
 * mount unless `allowUnsafeEval` is set; without it the card throws before the
 * host handshake. `zod/mini` re-exports the real implementation from the core.
 */
export const config = m.config
export const toJSONSchema = m.toJSONSchema

export const z = {
  any, array, boolean, config, custom, discriminatedUnion, enum: enum_, intersection,
  iso, literal, looseObject, never: never_, null: null_, number, object,
  optional, preprocess, record, strictObject, string, toJSONSchema,
  undefined: undefined_, union, unknown,
}
