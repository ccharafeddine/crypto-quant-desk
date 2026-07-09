"""Trading layer: the only code path that can move money.

Every order flows OrderService -> validation (limits) -> confirmation ->
routing (PaperBroker or Kraken REST) -> audit log. No module outside this
package may submit an order, and no code path here (or anywhere) may touch a
withdrawal endpoint.
"""
