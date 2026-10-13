// @vitest-environment node
import type * as m from 'zod/mini'

import { describe, expect, it } from 'vitest'

import * as shim from './zod-classic-shim.ts'

/**
 * TypeScript sees `zod/mini`'s types here, not the classic methods the Proxy
 * adds, so the helpers below reach those methods through `Reflect` rather than a
 * type assertion — which also puts every call through the Proxy, as the SDK's
 * own calls go.
 *
 * `zod-shim-parity.test.ts` covers whether the shim agrees with real zod on the
 * SDK's frames; this file covers each method and narrower on its own.
 */

/** `undefined` as data to parse, not an omitted argument. */
const ABSENT = undefined

const asObject = (value: unknown): object => {
  if (typeof value !== 'object' || value === null) throw new TypeError('expected a schema object')
  return value
}

const isSchema = (value: unknown): value is m.ZodMiniType =>
  typeof value === 'object' && value !== null && '_zod' in value

const asSchema = (value: unknown): m.ZodMiniType => {
  if (!isSchema(value)) throw new TypeError('expected a schema')
  return value
}

const jsonSchemaOf = (schema: unknown): object => asObject(shim.toJSONSchema(asSchema(schema)))

/** Invoke a classic method through the Proxy. */
const call = (schema: unknown, method: string, ...arguments_: readonly unknown[]): unknown => {
  const target = asObject(schema)
  const property: unknown = Reflect.get(target, method)
  if (typeof property !== 'function') throw new TypeError(`shim does not expose ${method}`)
  return Reflect.apply(property, target, arguments_)
}

const accepts = (schema: unknown, value: unknown): boolean => {
  const result = call(schema, 'safeParse', value)
  return typeof result === 'object' && result !== null && Reflect.get(result, 'success') === true
}

/** `call`, under the name that reads correctly at the chained call sites below. */
const chain = (schema: unknown, method: string, ...arguments_: readonly unknown[]): unknown =>
  call(schema, method, ...arguments_)

describe('factories', () => {
  it('builds each primitive the SDK imports', () => {
    expect(accepts(shim.string(), 'x')).toBe(true)
    expect(accepts(shim.string(), 1)).toBe(false)
    expect(accepts(shim.number(), 1)).toBe(true)
    expect(accepts(shim.boolean(), true)).toBe(true)
    expect(accepts(shim.any(), Symbol('s'))).toBe(true)
    expect(accepts(shim.unknown(), Symbol('s'))).toBe(true)
    expect(accepts(shim.never(), 'x')).toBe(false)
    expect(accepts(shim.null(), null)).toBe(true)
    expect(accepts(shim.undefined(), ABSENT)).toBe(true)
    expect(accepts(shim.literal('a'), 'a')).toBe(true)
    expect(accepts(shim.literal('a'), 'b')).toBe(false)
  })

  it('builds each composite the SDK imports', () => {
    expect(accepts(shim.array(shim.string()), ['a'])).toBe(true)
    expect(accepts(shim.array(shim.string()), [1])).toBe(false)
    expect(accepts(shim.object({ a: shim.string() }), { a: 'x' })).toBe(true)
    expect(accepts(shim.object({ a: shim.string() }), {})).toBe(false)
    expect(accepts(shim.looseObject({ a: shim.string() }), { a: 'x', extra: 1 })).toBe(true)
    expect(accepts(shim.strictObject({ a: shim.string() }), { a: 'x' })).toBe(true)
    expect(accepts(shim.strictObject({ a: shim.string() }), { a: 'x', extra: 1 })).toBe(false)
    expect(accepts(shim.union([shim.string(), shim.number()]), 1)).toBe(true)
    expect(accepts(shim.intersection(shim.object({ a: shim.string() }), shim.object({ b: shim.string() })), { a: 'x', b: 'y' })).toBe(true)
    expect(accepts(shim.record(shim.string(), shim.number()), { a: 1 })).toBe(true)
    expect(accepts(shim.optional(shim.string()), ABSENT)).toBe(true)
    expect(accepts(shim.enum({ a: 'a', b: 'b' }), 'a')).toBe(true)
    expect(accepts(shim.enum({ a: 'a', b: 'b' }), 'c')).toBe(false)
    expect(accepts(shim.custom(value => typeof value === 'string'), 'x')).toBe(true)
    expect(accepts(shim.iso.datetime(), '2026-01-26T00:00:00Z')).toBe(true)
  })

  it('discriminates a union on its literal key', () => {
    const schema = shim.discriminatedUnion('type', [
      shim.object({ a: shim.string(), type: shim.literal('a') }),
      shim.object({ type: shim.literal('b') }),
    ])

    expect(accepts(schema, { a: 'x', type: 'a' })).toBe(true)
    expect(accepts(schema, { type: 'a' })).toBe(false)
    expect(accepts(schema, { type: 'c' })).toBe(false)
  })

  it('runs preprocess before the inner schema', () => {
    const schema = shim.preprocess(String, shim.string())

    expect(accepts(schema, 5)).toBe(true)
  })

  it('exposes config and toJSONSchema, which are called rather than chained', () => {
    // Without `z.config` the card throws before the host handshake.
    expect(typeof shim.config).toBe('function')
    expect(() => shim.config({})).not.toThrow()
    expect(Reflect.get(shim.toJSONSchema(shim.string()), 'type')).toBe('string')
  })

  it('offers the same factories as named exports and on the aggregate', () => {
    // sdk/types.js reaches them as a namespace, ext-apps as the `z` object.
    expect(shim.z.string).toBe(shim.string)
    expect(shim.z.config).toBe(shim.config)
  })
})

describe('chained methods', () => {
  it('wraps and unwraps optionality', () => {
    expect(accepts(chain(shim.string(), 'optional'), ABSENT)).toBe(true)
    expect(accepts(chain(shim.string(), 'nullable'), null)).toBe(true)
    expect(accepts(chain(shim.string(), 'array'), ['a'])).toBe(true)
    // Parse rather than accept: the inner optional already accepts `undefined`,
    // so only the substituted value proves `default` did anything.
    expect(call(chain(shim.optional(shim.string()), 'default', 'd'), 'parse', ABSENT)).toBe('d')
    expect(accepts(chain(shim.number(), 'catch', 0), 'not a number')).toBe(true)
  })

  it('composes schemas', () => {
    const left = shim.object({ a: shim.string() })

    expect(accepts(chain(left, 'and', shim.object({ b: shim.string() })), { a: 'x', b: 'y' })).toBe(true)
    expect(accepts(chain(left, 'and', shim.object({ b: shim.string() })), { a: 'x' })).toBe(false)
    expect(accepts(chain(left, 'or', shim.number()), 1)).toBe(true)
    expect(accepts(chain(left, 'merge', shim.object({ b: shim.number() })), { a: 'x', b: 1 })).toBe(true)
    expect(accepts(chain(left, 'merge', shim.object({ b: shim.number() })), { a: 'x', b: 'y' })).toBe(false)
    expect(accepts(chain(left, 'extend', { b: shim.number() }), { a: 'x', b: 1 })).toBe(true)
    expect(accepts(chain(left, 'catchall', shim.number()), { a: 'x', b: 1 })).toBe(true)
    expect(accepts(chain(left, 'catchall', shim.number()), { a: 'x', b: 'y' })).toBe(false)
  })

  it('reshapes objects', () => {
    const schema = shim.object({ a: shim.string(), b: shim.string() })

    expect(accepts(chain(schema, 'partial'), {})).toBe(true)
    expect(accepts(chain(schema, 'partial', { a: true }), { b: 'y' })).toBe(true)
    expect(accepts(chain(schema, 'partial', { a: true }), { a: 'x' })).toBe(false)
    expect(accepts(chain(schema, 'pick', { a: true }), { a: 'x' })).toBe(true)
    expect(accepts(chain(schema, 'pick', { a: true }), { a: 1 })).toBe(false)
    // Classic zod skips falsy mask entries rather than coercing them to true.
    expect(accepts(chain(schema, 'pick', { a: true, b: false }), { a: 'x' })).toBe(true)
    expect(accepts(chain(schema, 'partial', { a: false }), { b: 'y' })).toBe(false)
    // Chained onto a strict object, not a plain one: a plain object already
    // accepts and strips unknown keys, so a no-op loose/passthrough would pass.
    expect(accepts(chain(shim.strictObject({ a: shim.string() }), 'loose'), { a: 'x', extra: 1 })).toBe(true)
    expect(accepts(chain(shim.strictObject({ a: shim.string() }), 'passthrough'), { a: 'x', extra: 1 })).toBe(true)
    expect(accepts(chain(shim.looseObject({ a: shim.string() }), 'strict'), { a: 'x', extra: 1 })).toBe(false)

    // strip and loose both accept the extra key; only the parsed output tells
    // them apart, so assert on that rather than on acceptance.
    expect(call(shim.looseObject({ a: shim.string() }), 'parse', { a: 'x', extra: 1 })).toStrictEqual({ a: 'x', extra: 1 })
    expect(call(chain(shim.looseObject({ a: shim.string() }), 'strip'), 'parse', { a: 'x', extra: 1 })).toStrictEqual({ a: 'x' })
  })

  it('applies string checks', () => {
    expect(accepts(chain(shim.string(), 'startsWith', 'ab'), 'abc')).toBe(true)
    expect(accepts(chain(shim.string(), 'startsWith', 'ab'), 'xbc')).toBe(false)
    expect(accepts(chain(shim.string(), 'url'), 'https://example.com')).toBe(true)
    expect(accepts(chain(shim.string(), 'url'), 'not a url')).toBe(false)
  })

  it('applies numeric checks', () => {
    expect(accepts(chain(shim.number(), 'int'), 1)).toBe(true)
    expect(accepts(chain(shim.number(), 'int'), 1.5)).toBe(false)
    expect(accepts(chain(shim.number(), 'nonnegative'), 0)).toBe(true)
    expect(accepts(chain(shim.number(), 'nonnegative'), -1)).toBe(false)
  })

  it('runs a refinement and a transform', () => {
    expect(accepts(chain(shim.string(), 'refine', (value: unknown) => value === 'ok'), 'ok')).toBe(true)
    expect(accepts(chain(shim.string(), 'refine', (value: unknown) => value === 'ok'), 'no')).toBe(false)
    expect(accepts(chain(shim.string(), 'transform', (value: unknown) => value), 'x')).toBe(true)
  })

  it('leaves readonly as the schema it was given', () => {
    expect(accepts(chain(shim.string(), 'readonly'), 'x')).toBe(true)
  })

  it('carries a description through to the JSON Schema a host is shown', () => {
    // zod resolves descriptions by object identity, so the Proxy has to carry
    // the registry entry across or a card's tool schemas reach the host bare.
    const described = chain(shim.string(), 'describe', 'a string')

    expect(accepts(described, 'x')).toBe(true)
    expect(Reflect.get(jsonSchemaOf(described), 'description')).toBe('a string')
  })

  it('survives a reshape chained after it, as classic zod does', () => {
    // Routing describe through `.check()` would leave a `def.checks` entry, and
    // pick/merge/partial reject any schema carrying one as refinement-bearing.
    const described = chain(shim.object({ a: shim.string(), b: shim.string() }), 'describe', 'an object')

    expect(accepts(chain(described, 'pick', { a: true }), { a: 'x' })).toBe(true)
    expect(accepts(chain(described, 'merge', shim.object({ c: shim.number() })), { a: 'x', b: 'y', c: 1 })).toBe(true)
    expect(accepts(chain(described, 'partial'), {})).toBe(true)
  })

  it('carries a description on a nested field too', () => {
    // The field is reached through the parent's shape, so the wrapped child has
    // to resolve its own registry entry.
    const schema = shim.object({ a: asSchema(chain(shim.string(), 'describe', 'the field')) })
    const properties = asObject(Reflect.get(jsonSchemaOf(schema), 'properties'))

    expect(Reflect.get(asObject(Reflect.get(properties, 'a')), 'description')).toBe('the field')
  })
})

describe('object policy', () => {
  it('takes the incoming schema policy on merge, not the receiver\'s', () => {
    const loose = shim.looseObject({ a: shim.string() })
    const strict = shim.strictObject({ b: shim.string() })

    expect(accepts(chain(loose, 'merge', strict), { a: 'x', b: 'y', extra: 1 })).toBe(false)
    expect(accepts(chain(strict, 'merge', loose), { a: 'x', b: 'y', extra: 1 })).toBe(true)
  })

  it('keeps a refinement chained before a policy change', () => {
    // Rebuilding the object from its shape would drop this refinement, and no
    // accept/reject comparison against the SDK's own schemas would show it.
    const refined = chain(
      shim.object({ a: shim.string() }),
      'refine',
      (value: unknown) => typeof value === 'object' && value !== null && Reflect.get(value, 'a') === 'ok',
    )

    expect(accepts(chain(refined, 'loose'), { a: 'ok', extra: 1 })).toBe(true)
    expect(accepts(chain(refined, 'loose'), { a: 'no', extra: 1 })).toBe(false)
    expect(accepts(chain(refined, 'strict'), { a: 'no' })).toBe(false)
  })
})

describe('min and max dispatch', () => {
  it('measures length for strings', () => {
    expect(accepts(chain(shim.string(), 'min', 2), 'ab')).toBe(true)
    expect(accepts(chain(shim.string(), 'min', 2), 'a')).toBe(false)
    expect(accepts(chain(shim.string(), 'max', 2), 'abc')).toBe(false)
  })

  it('measures length for arrays rather than comparing magnitude', () => {
    // The bug this guards: a numeric bound on an array compares an object to a
    // number, which is NaN, so every value fails rather than the bound applying.
    // `set`/`map`/`file` sit in SIZED_TYPES defensively — the shim exports no
    // factory for them, so no card schema can be of those types yet.
    expect(accepts(chain(shim.array(shim.string()), 'max', 2), ['a', 'b'])).toBe(true)
    expect(accepts(chain(shim.array(shim.string()), 'max', 2), ['a', 'b', 'c'])).toBe(false)
    expect(accepts(chain(shim.array(shim.string()), 'min', 1), ['a'])).toBe(true)
    expect(accepts(chain(shim.array(shim.string()), 'min', 1), [])).toBe(false)
  })

  it('compares magnitude for numbers', () => {
    expect(accepts(chain(shim.number(), 'max', 2), 2)).toBe(true)
    expect(accepts(chain(shim.number(), 'max', 2), 3)).toBe(false)
    expect(accepts(chain(shim.number(), 'min', 2), 1)).toBe(false)
  })
})

describe('proxy behaviour', () => {
  it('re-derives the classic literal value getter', () => {
    // ext-apps keys its handler registry off `schema.shape.method.value`, which
    // zod/mini does not define.
    expect(Reflect.get(asObject(shim.literal('ui/initialize')), 'value')).toBe('ui/initialize')
  })

  it('has no literal value for a non-literal schema', () => {
    expect(Reflect.get(asObject(shim.string()), 'value')).toBeUndefined()
  })

  it('refuses a single value for a multi-value literal, as classic zod does', () => {
    const either = shim.literal(['a', 'b'])

    expect(accepts(either, 'a')).toBe(true)
    expect(accepts(either, 'c')).toBe(false)
    const readValue = (): void => {
      Reflect.get(asObject(either), 'value')
    }

    expect(readValue).toThrow(/multiple values/)
  })

  it('passes through non-function properties and binds real methods to the target', () => {
    expect(Reflect.get(asObject(shim.string()), 'def')).toMatchObject({ type: 'string' })
    expect(accepts(shim.string(), 'x')).toBe(true)
  })

  it('exposes the object shape the SDK reads', () => {
    const shape: unknown = Reflect.get(asObject(shim.object({ a: shim.string() })), 'shape')

    expect(Object.keys(asObject(shape))).toStrictEqual(['a'])
  })
})

describe('argument narrowing', () => {
  it('rejects a non-object schema where the method needs one', () => {
    expect(() => chain(shim.string(), 'extend', {})).toThrow(/expected an object schema/)
  })

  it('rejects a non-schema where the method needs a schema', () => {
    expect(() => chain(shim.string(), 'or', 'not a schema')).toThrow(/expected a schema argument/)
    expect(() => chain(shim.string(), 'or', null)).toThrow(/expected a schema argument/)
    expect(() => chain(shim.string(), 'or', { notZod: true })).toThrow(/expected a schema argument/)
  })

  it('rejects a malformed shape or mask', () => {
    expect(() => chain(shim.object({}), 'extend', 'not a shape')).toThrow(/expected a shape argument/)
    expect(() => chain(shim.object({}), 'extend', { b: 'not a schema' })).toThrow(/expected a schema argument/)
    expect(() => chain(shim.object({ a: shim.string() }), 'pick', 'not a mask')).toThrow(/expected a mask argument/)
  })

  it('rejects a bound, prefix, or predicate of the wrong type', () => {
    expect(() => chain(shim.string(), 'min', 'not a number')).toThrow(/expected a numeric argument/)
    expect(() => chain(shim.string(), 'startsWith', 1)).toThrow(/expected a string argument/)
    expect(() => chain(shim.string(), 'refine', 'not a function')).toThrow(/expected a function argument/)
  })
})
