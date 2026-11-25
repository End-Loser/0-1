"""Keep the original work gate; reject endpoint cancellation in reconstruction."""
from decimal import Decimal, localcontext
import unittest

import numpy as np

from verify_copper_driven_history import source_coefficients


def high_precision_envelope(fraction):
    with localcontext() as context:
        context.prec = 70
        pi = Decimal("3.141592653589793238462643383279502884197169399375105820974944592307816")
        x = min(Decimal.from_float(fraction), 1 - Decimal.from_float(fraction)) * pi
        term = value = x
        for n in range(1, 25):
            term *= -x * x / Decimal((2 * n) * (2 * n + 1))
            value += term
        return float(value * value)


class EnvelopeTests(unittest.TestCase):
    def test_both_endpoints_with_high_precision_reference(self):
        for fraction in (0.0002, 0.9998, 1e-7, 1 - 1e-7):
            pulse = dict(mode=0, start_s=0., end_s=1., amplitude=1., phase_rad=0.)
            observed = source_coefficients(fraction, np.zeros(3), [pulse])[0]
            reference = high_precision_envelope(fraction)
            self.assertLess(abs(observed / reference - 1), 1e-11)

    def test_original_one_ps_endpoint_cancellation_retained(self):
        t, start, end = 1.19995e-8, 9.5e-9, 12e-9
        fraction = (t - start) / (end - start)
        reference = high_precision_envelope(fraction)
        old = (1 - np.cos(2 * np.pi * fraction)) / 2
        pulse = dict(mode=0, start_s=start, end_s=end, amplitude=1., phase_rad=0.)
        stable = source_coefficients(t, np.zeros(3), [pulse])[0]
        self.assertGreater(abs(old / reference - 1), 1e-11)
        self.assertLess(abs(stable / reference - 1), 1e-11)

    def test_support_sign_and_overlap_preserved(self):
        pulse = dict(mode=1, start_s=1., end_s=2., amplitude=-2., phase_rad=0.)
        for t in (0., 1., 2., 3.):
            np.testing.assert_array_equal(source_coefficients(t, np.zeros(3), [pulse]), np.zeros(3))
        np.testing.assert_array_equal(source_coefficients(1.5, np.zeros(3), [pulse, pulse]),
                                      [0., -4., 0.])


if __name__ == "__main__":
    unittest.main()
