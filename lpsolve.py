"""Exact rational two-phase simplex over Fraction.

Standard form handled here:
    A z = b,  z >= 0,  minimize c^T z
with an automatic artificial-variable phase I.  Everything stays rational;
no floats and no tolerances are used anywhere.

Single tableau convention used in both phases.  For basis B with
y^T = c_B^T B^-1 the objective row stores
    row0[j]   = y^T A_j - c_j   (the NEGATED reduced cost of column j)
    row0[-1]  = y^T b           (the current objective value)
so an entering column has a strictly positive row0 coefficient, and every
pivot is the ordinary row operation
    row0 <- row0 - row0[k] * T[r]      (with T[r][k] normalized to 1).
Constraint rows are canonical equalities B^-1 A z = B^-1 b.  Bland's rule
(smallest entering index; smallest basic-variable index on ratio ties)
guarantees termination under exact arithmetic.
"""

from fractions import Fraction as F


class Unbounded(Exception):
    """Raised when a feasible minimization LP is unbounded below."""

    def __init__(self, point, ray, value):
        super().__init__("linear program unbounded below")
        self.point = point
        self.ray = ray
        self.value = value


def _row_op(rows, r, k, row0):
    """Normalize row r on column k and eliminate column k everywhere."""
    pivot = rows[r][k]
    rows[r] = [x / pivot for x in rows[r]]
    for i, row in enumerate(rows):
        if i != r and row[k] != 0:
            f = row[k]
            rows[i] = [x - f * y for x, y in zip(row, rows[r])]
    f = row0[k]
    row0[:] = [x - f * y for x, y in zip(row0, rows[r])]


def standard_simplex(A, b, c):
    """Solve min c^T z s.t. A z = b, z >= 0 over exact rationals.

    Returns ("optimal", solution, row_duals) or
    ("infeasible", None, None), or raises Unbounded with a feasible point
    and non-negative descent ray.  Artificial columns are appended last,
    one per row.
    """
    m = len(A)
    n = len(A[0]) if m else len(c)
    if m == 0:
        if any(F(x) != 0 for x in c):
            k = next(j for j, x in enumerate(c) if x != 0)
            ray = [F(0)] * n
            ray[k] = F(1 if c[k] < 0 else -1)
            raise Unbounded([F(0)] * n, ray, F(0))
        return "optimal", [F(0)] * n, []

    T = [[F(x) for x in row] for row in A]
    b = [F(x) for x in b]
    c = [F(x) for x in c]
    total = n + m

    # Flip negative-RHS equalities BEFORE adding artificials so the unit
    # artificial basis starts feasible and identity.  Record the flip sign
    # so callers can map equality duals back to the original row ordering.
    flip = [F(1)] * m
    for i in range(m):
        if b[i] < 0:
            T[i] = [-x for x in T[i]]
            b[i] = -b[i]
            flip[i] = F(-1)
    for i in range(m):
        T[i] += [F(1 if j == i else 0) for j in range(m)] + [b[i]]
    basis = list(range(n, total))

    # Phase I row: objective w = sum of artificials.  On the identity
    # artificial basis y = (1,...,1), hence row0 is the sum of constraint
    # rows with the artificial entries zeroed (their c is 1).
    row0 = [F(0)] * (total + 1)
    for i in range(m):
        for j in range(total + 1):
            row0[j] += T[i][j]
    for j in range(n, total):
        row0[j] = F(0)

    def entering(limit):
        for j in range(limit):  # Bland: smallest eligible index
            if row0[j] > 0:
                return j
        return None

    def ratio_row(k):
        best = None
        for i in range(len(T)):
            aik = T[i][k]
            if aik > 0:
                ratio = T[i][-1] / aik
                if best is None or ratio < best[0] or (
                        ratio == best[0] and basis[i] < basis[best[1]]):
                    best = (ratio, i)
        return None if best is None else best[1]

    # ---- Phase I: real columns only may enter ----
    while True:
        k = entering(n)
        if k is None:
            break
        r = ratio_row(k)
        if r is None:
            raise RuntimeError("phase I unbounded, malformed standard form")
        _row_op(T, r, k, row0)
        basis[r] = k

    if row0[-1] > 0:
        return "infeasible", None, None

    # Expel remaining basic artificials on real columns; drop rows that
    # have degenerated to exact zero equalities, then strip all artificial
    # columns so phase II can never pivot them back into the basis.
    keep = [True] * m
    for i in range(m):
        if basis[i] >= n:
            col = next((j for j in range(n)
                        if T[i][j] != 0 and j not in basis), None)
            if col is None:
                keep[i] = False
                continue
            _row_op(T, i, col, row0)
            basis[i] = col
    T = [T[i][:n] + [T[i][-1]] for i in range(m) if keep[i]]
    basis = [basis[i] for i in range(m) if keep[i]]
    m2 = len(T)

    # ---- Phase II: explicit dual, raw reduced costs (enter on negative) ----
    # Given the current feasible basis, solve B^T y = c_B for the equality
    # duals; reduced cost of column j is c_j - y^T A_j.  We keep the
    # objective value v = y^T b explicitly instead of carrying it in the
    # tableau, so the constraint tableau only needs ordinary pivot row ops.
    def current_dual():
        B = [[T[i][j] for i in range(m2)] for j in basis]
        cB = [c[j] for j in basis]
        return _solve_transposed(B, cB)

    y2 = current_dual()

    def reduced_costs(yv):
        return [c[j] - sum(yv[i] * T[i][j] for i in range(m2))
                for j in range(n)]

    while True:
        rc = reduced_costs(y2)
        # Bland: smallest-index variable (all standard-form columns,
        # including slacks) with a strictly negative reduced cost.
        k = next((j for j in range(n) if rc[j] < 0), None)
        if k is None:
            break
        r = ratio_row(k)
        if r is None:
            point = [F(0)] * n
            for i, bcol in enumerate(basis):
                point[bcol] = T[i][-1]
            ray = [F(0)] * n
            ray[k] = F(1)
            for i, bcol in enumerate(basis):
                ray[bcol] = -T[i][k]
            value = sum(y2[i] * T[i][-1] for i in range(m2))
            raise Unbounded(point, ray, value)
        # Pivot the constraint tableau only; the dual is recomputed exactly
        # from the new basis afterwards (no incremental objective row to
        # drift out of consistency).
        pivot = T[r][k]
        T[r] = [x / pivot for x in T[r]]
        for i, row in enumerate(T):
            if i != r and row[k] != 0:
                f = row[k]
                T[i] = [x - f * z for x, z in zip(row, T[r])]
        basis[r] = k
        y2 = current_dual()

    point = [F(0)] * n
    for i, bcol in enumerate(basis):
        point[bcol] = T[i][-1]

    # Equality duals in ORIGINAL row coordinates.
    # On the working (possibly row-flipped) matrix A_work with optimal
    # basis B_work, the equality dual y_work is fixed by zero reduced cost
    # on basic columns: B_work^T y_work = c_B.  Row flips only relabel the
    # equalities, so each flipped row carries the negated multiplier when
    # mapped back to the original rows.
    A_work = [[F(A[i][j]) * flip[i] for j in range(n)] for i in range(m)]
    retained = [i for i in range(m) if keep[i]]
    # B_work has one row per retained working equality and one column per
    # basic variable: B_work[i][q] = coefficient of basis variable q in row i.
    B_work = [[A_work[i][bcol] for bcol in basis] for i in retained]
    cB = [c[j] for j in basis]
    y_work = _solve_transposed(B_work, cB)
    y, slot = [], 0
    for i in range(m):
        if keep[i]:
            y.append(flip[i] * y_work[slot])
            slot += 1
        else:
            y.append(F(0))
    return "optimal", point, y


def _solve_transposed(M, rhs):
    """Solve M^T y = rhs with exact Gauss-Jordan elimination.

    M is an invertible simplex basis matrix; the augmented matrix is built
    from M^T and reduced to the identity.
    """
    n = len(rhs)
    A = [[M[j][i] for j in range(n)] + [rhs[i]] for i in range(n)]
    for col in range(n):
        pivot = next(i for i in range(col, n) if A[i][col] != 0)
        A[col], A[pivot] = A[pivot], A[col]
        p = A[col][col]
        A[col] = [x / p for x in A[col]]
        for i in range(n):
            if i != col and A[i][col] != 0:
                f = A[i][col]
                A[i] = [x - f * z for x, z in zip(A[i], A[col])]
    return [A[i][-1] for i in range(n)]
