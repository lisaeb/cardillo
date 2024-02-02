from abc import ABC, abstractmethod
from cachetools import cachedmethod, LRUCache
from cachetools.keys import hashkey
import numpy as np
import warnings

from cardillo.math.algebra import norm, cross3, ax2skew
from cardillo.math.approx_fprime import approx_fprime
from cardillo.math.rotations import Log_SO3_quat, T_SO3_inv_quat, T_SO3_inv_quat_P
from cardillo.utility.coo_matrix import CooMatrix

from ._base_export import RodExportBase
from ._cross_section import CrossSectionInertias
from .discretization.lagrange import LagrangeKnotVector
from .discretization.mesh1D import Mesh1D


class CosseratRod(RodExportBase, ABC):
    def __init__(
        self,
        cross_section,
        material_model,
        nelement,
        polynomial_degree,
        nquadrature,
        Q,
        q0=None,
        u0=None,
        nquadrature_dyn=None,
        cross_section_inertias=CrossSectionInertias(),
        **kwargs,
    ):
        """Base class for Petrov-Galerkin Cosserat rod formulations with 
        quaternions for the nodal orientation parametrization.

        Parameters
        ----------
        cross_section: CrossSection
            Geometric cross-section properties: area, first and second moments 
            of area.
        material_model: RodMaterialModel
            Constitutive law of Cosserat rod which relates the rod strain 
            measures K_Gamma and K_Kappa with the contact forces K_n and couples
            K_m in the cross-section-fixed K-basis.
        nelement: int
            Number of rod elements.
        polynomial_degree: int
            Polynomial degree of ansatz and test functions.
        nquadrature: int
            Number of quadrature points.
        Q: np.array(self.nq)
            Generalized position coordinates of rod in a stress-free reference 
            state. Q is a collection of nodal generalized position coordinates, 
            which are given by the Cartesian coordinates of the nodal centerline 
            point r_OP_i in R^3 together with non-unit quaternions p_i in R^4 
            representing the nodal cross-section orientation.
        q0: np.array(self.nq)
            Initial generalized position coordinates of rod at time t0.
        u0: np.array(self.nu)
            Initial generalized velocity coordinates of rod at time t0. 
            Generalized velocity coordinates u0 is a collection of the nodal 
            generalized velocity coordinates, which are given by the nodal 
            centerline velocity v_P_i in R^3 together with the cross-section 
            angular velocity represented in the cross-section-fixed K-basis 
            K_omega_IK.
        nquadratur_dyn: int
            Number of quadrature points to integrate dynamical and external 
            virtual work functionals.
        cross_section_inertias: CrossSectionInertias
            Inertia properties of cross-sections: Cross-section mass density and 
            Cross-section inertia tensor represented in the cross-section-fixed 
            K-Basis.

        """
        super().__init__(cross_section)

        # rod properties
        self.material_model = material_model
        self.cross_section_inertias = cross_section_inertias

        # distinguish between inertia quadrature and other parts
        self.nquadrature = nquadrature
        if nquadrature_dyn is None:
            self.nquadrature_dyn = nquadrature
        else:
            self.nquadrature_dyn = nquadrature_dyn
        # print(f"nquadrature_dyn: {nquadrature_dyn}")
        # print(f"nquadrature: {nquadrature}")
        self.nelement = nelement

        self._eval_cache = LRUCache(maxsize=nquadrature + 10)
        self._deval_cache = LRUCache(maxsize=nquadrature + 10)

        ##############################################################
        # discretization parameters centerline (r) and orientation (p)
        ##############################################################
        self.polynomial_degree_r = polynomial_degree
        self.polynomial_degree_p = polynomial_degree
        self.knot_vector_r = LagrangeKnotVector(self.polynomial_degree_r, nelement)
        self.knot_vector_p = LagrangeKnotVector(self.polynomial_degree_p, nelement)

        # build mesh objects
        self.mesh_r = Mesh1D(
            self.knot_vector_r,
            nquadrature,
            dim_q=3,
            derivative_order=1,
            basis="Lagrange",
            quadrature="Gauss",
        )
        self.mesh_r_dyn = Mesh1D(
            self.knot_vector_r,
            nquadrature_dyn,
            dim_q=3,
            derivative_order=1,
            basis="Lagrange",
            quadrature="Gauss",
        )
        self.mesh_p = Mesh1D(
            self.knot_vector_p,
            nquadrature,
            dim_q=4,
            derivative_order=1,
            basis="Lagrange",
            quadrature="Gauss",
            dim_u=3,
        )
        self.mesh_p_dyn = Mesh1D(
            self.knot_vector_p,
            nquadrature_dyn,
            dim_q=4,
            derivative_order=1,
            basis="Lagrange",
            quadrature="Gauss",
            dim_u=3,
        )

        # total number of nodes
        self.nnodes_r = self.mesh_r.nnodes
        self.nnodes_p = self.mesh_p.nnodes

        # number of nodes per element
        self.nnodes_element_r = self.mesh_r.nnodes_per_element
        self.nnodes_element_p = self.mesh_p.nnodes_per_element

        # total number of generalized position coordinates
        self.nq_r = self.mesh_r.nq
        self.nq_p = self.mesh_p.nq
        self.nq = self.nq_r + self.nq_p

        # total number of generalized velocity coordinates
        self.nu_r = self.mesh_r.nu
        self.nu_p = self.mesh_p.nu
        self.nu = self.nu_r + self.nu_p

        # number of generalized position coordinates per element
        self.nq_element_r = self.mesh_r.nq_per_element
        self.nq_element_p = self.mesh_p.nq_per_element
        self.nq_element = self.nq_element_r + self.nq_element_p

        # number of generalized velocity coordinates per element
        self.nu_element_r = self.mesh_r.nu_per_element
        self.nu_element_p = self.mesh_p.nu_per_element
        self.nu_element = self.nu_element_r + self.nu_element_p

        # global element connectivity
        # qe = q[elDOF_*[e]] "q^e = C_q,e q"
        self.elDOF_r = self.mesh_r.elDOF
        self.elDOF_p = self.mesh_p.elDOF + self.nq_r
        # ue = u[elDOF_*_u[e]] "u^e = C_u,e u"
        # TODO: maybe rename to elDOF_u_r and elDOF_u_p
        self.elDOF_r_u = self.mesh_r.elDOF_u
        self.elDOF_p_u = self.mesh_p.elDOF_u + self.nu_r

        # global nodal connectivity
        self.nodalDOF_r = self.mesh_r.nodalDOF
        self.nodalDOF_p = self.mesh_p.nodalDOF + self.nq_r
        # TODO: maybe rename to nodalDOF_u_r and nodalDOF_u_r
        self.nodalDOF_r_u = self.mesh_r.nodalDOF_u
        self.nodalDOF_p_u = self.mesh_p.nodalDOF_u + self.nu_r

        # nodal connectivity on element level
        # r_OP_i^e = C_r,i^e * C_q,e q = C_r,i^e * q^e
        # r_OPi = qe[nodalDOF_element_r[i]]
        self.nodalDOF_element_r = self.mesh_r.nodalDOF_element
        self.nodalDOF_element_p = self.mesh_p.nodalDOF_element + self.nq_element_r
        # TODO: maybe rename it: switch r and u and p and u.
        # v_P_i^e = C_u,i^e * C_u,e q = C_u,i^e * u^e
        # v_Pi = ue[nodalDOF_element_r[i]]
        self.nodalDOF_element_r_u = self.mesh_r.nodalDOF_element_u
        self.nodalDOF_element_p_u = (
            self.mesh_p.nodalDOF_element_u + self.nu_element_r
        )

        # build global elDOF connectivity matrix
        self.elDOF = np.zeros((nelement, self.nq_element), dtype=int)
        self.elDOF_u = np.zeros((nelement, self.nu_element), dtype=int)
        for el in range(nelement):
            self.elDOF[el, : self.nq_element_r] = self.elDOF_r[el]
            self.elDOF[el, self.nq_element_r :] = self.elDOF_p[el]
            self.elDOF_u[el, : self.nu_element_r] = self.elDOF_r_u[el]
            self.elDOF_u[el, self.nu_element_r :] = self.elDOF_p_u[el]

        # shape functions and their first derivatives
        self.N_r = self.mesh_r.N
        self.N_r_xi = self.mesh_r.N_xi
        self.N_p = self.mesh_p.N
        self.N_p_xi = self.mesh_p.N_xi

        self.N_r_dyn = self.mesh_r_dyn.N
        self.N_p_dyn = self.mesh_p_dyn.N

        # quadrature points
        # note: compliance equations use the same quadrature points
        self.qp = self.mesh_r.qp  # quadrature points
        self.qw = self.mesh_r.wp  # quadrature weights
        self.qp_dyn = self.mesh_r_dyn.qp  # quadrature points for dynamics
        self.qw_dyn = self.mesh_r_dyn.wp  # quadrature weights for dynamics

        # reference generalized coordinates, initial coordinates and initial velocities
        self.Q = Q
        self.q0 = Q.copy() if q0 is None else q0
        self.u0 = np.zeros(self.nu, dtype=float) if u0 is None else u0

        # evaluate shape functions at specific xi
        self.basis_functions_r = self.mesh_r.eval_basis
        self.basis_functions_p = self.mesh_p.eval_basis

        # quaternion constraints
        dim_g_S = 1
        self.nla_S = self.nnodes_p * dim_g_S
        self.nodalDOF_la_S = np.arange(self.nla_S).reshape(self.nnodes_p, dim_g_S)
        self.la_S0 = np.zeros(self.nla_S, dtype=float)

        self.set_reference_strains(self.Q)

    def set_reference_strains(self, Q):
        self.Q = Q.copy()

        # precompute values of the reference configuration in order to save computation time
        # J in Harsch2020b (5)
        self.J = np.zeros((self.nelement, self.nquadrature), dtype=float)
        self.J_dyn = np.zeros((self.nelement, self.nquadrature_dyn), dtype=float)
        # dilatation and shear strains of the reference configuration
        self.K_Gamma0 = np.zeros((self.nelement, self.nquadrature, 3), dtype=float)
        # curvature of the reference configuration
        self.K_Kappa0 = np.zeros((self.nelement, self.nquadrature, 3), dtype=float)

        for el in range(self.nelement):
            qe = self.Q[self.elDOF[el]]

            for i in range(self.nquadrature):
                # current quadrature point
                qpi = self.qp[el, i]

                # evaluate required quantities
                _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

                # length of reference tangential vector
                J = norm(K_Gamma_bar)

                # axial and shear strains
                K_Gamma = K_Gamma_bar / J

                # torsional and flexural strains
                K_Kappa = K_Kappa_bar / J

                # safe precomputed quantities for later
                self.J[el, i] = J
                self.K_Gamma0[el, i] = K_Gamma
                self.K_Kappa0[el, i] = K_Kappa

            for i in range(self.nquadrature_dyn):
                # current quadrature point
                qpi = self.qp_dyn[el, i]

                # evaluate required quantities
                # _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

                _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

                # length of reference tangential vector
                self.J_dyn[el, i] = norm(K_Gamma_bar)

    @staticmethod
    def straight_configuration(
        nelement,
        L,
        polynomial_degree=1,
        r_OP=np.zeros(3, dtype=float),
        A_IK=np.eye(3, dtype=float),
    ):
        nnodes_r = polynomial_degree * nelement + 1
        nnodes_p = polynomial_degree * nelement + 1

        x0 = np.linspace(0, L, num=nnodes_r)
        y0 = np.zeros(nnodes_r)
        z0 = np.zeros(nnodes_r)

        r0 = np.vstack((x0, y0, z0))
        for i in range(nnodes_r):
            r0[:, i] = r_OP + A_IK @ r0[:, i]

        # reshape generalized coordinates to nodal ordering
        q_r = r0.reshape(-1, order="C")

        # we have to extract the rotation vector from the given rotation matrix
        # and set its value for each node
        p = Log_SO3_quat(A_IK)
        q_p = np.repeat(p, nnodes_p)

        return np.concatenate([q_r, q_p])

    @staticmethod
    def deformed_configuration(
        nelement,
        curve,
        dcurve,
        ddcurve,
        xi1,
        polynomial_degree,
        r_OP=np.zeros(3, dtype=float),
        A_IK=np.eye(3, dtype=float),
    ):
        nnodes_r = polynomial_degree * nelement + 1

        xis = np.linspace(0, xi1, nnodes_r)

        # nodal positions
        r0 = np.zeros((3, nnodes_r))
        P0 = np.zeros((4, nnodes_r))

        for i, xii in enumerate(xis):
            r0[:, i] = r_OP + A_IK @ curve(xii)
            A_KC = np.zeros((3, 3))
            A_KC[:, 0] = dcurve(xii) / norm(dcurve(xii))
            A_KC[:, 1] = ddcurve(xii) / norm(ddcurve(xii))
            A_KC[:, 2] = cross3(A_KC[:, 0], A_KC[:, 1])
            A_IC = A_IK @ A_KC
            P0[:, i] = Log_SO3_quat(A_IC)

        for i in range(nnodes_r - 1):
            inner = P0[:, i] @ P0[:, i + 1]
            # print(f"i: {i}")
            if inner < 0:
                # print("wrong hemisphere!")
                P0[:, i + 1] *= -1
            # else:
            # print(f"correct hemisphere")

        # reshape nodal positions for generalized coordinates tuple
        q_r = r0.reshape(-1, order="C")
        q_P = P0.reshape(-1, order="C")

        return np.concatenate([q_r, q_P])

    @staticmethod
    def straight_initial_configuration(
        nelement,
        L,
        polynomial_degree=1,
        r_OP=np.zeros(3, dtype=float),
        A_IK=np.eye(3, dtype=float),
        v_P=np.zeros(3, dtype=float),
        K_omega_IK=np.zeros(3, dtype=float),
    ):
        nnodes_r = polynomial_degree * nelement + 1
        nnodes_p = polynomial_degree * nelement + 1

        x0 = np.linspace(0, L, num=nnodes_r)
        y0 = np.zeros(nnodes_r)
        z0 = np.zeros(nnodes_r)

        r_OC0 = np.vstack((x0, y0, z0))
        for i in range(nnodes_r):
            r_OC0[:, i] = r_OP + A_IK @ r_OC0[:, i]

        # reshape generalized coordinates to nodal ordering
        q_r = r_OC0.reshape(-1, order="C")

        p = Log_SO3_quat(A_IK)
        q_p = np.repeat(p, nnodes_p)

        ################################
        # compute generalized velocities
        ################################
        # centerline velocities
        v_C0 = np.zeros_like(r_OC0, dtype=float)
        for i in range(nnodes_r):
            v_C0[:, i] = v_P + cross3(A_IK @ K_omega_IK, (r_OC0[:, i] - r_OC0[:, 0]))

        # reshape generalized velocities to nodal ordering
        u_r = v_C0.reshape(-1, order="C")

        # all nodes share the same angular velocity
        u_p = np.repeat(K_omega_IK, nnodes_p)

        q0 = np.concatenate([q_r, q_p])
        u0 = np.concatenate([u_r, u_p])

        return q0, u0

    def element_number(self, xi):
        """Compute element number from given xi."""
        return self.knot_vector_r.element_number(xi)[0]

    ############################
    # export of centerline nodes
    ############################
    def nodes(self, q):
        q_body = q[self.qDOF]
        return np.array([q_body[nodalDOF] for nodalDOF in self.nodalDOF_r]).T

    ##################
    # abstract methods
    ##################
    @abstractmethod
    def _eval(self, qe, xi, mixed=False):
        """Compute (r_OP, A_IK, K_Gamma_bar, K_Kappa_bar)."""
        ...

    # @abstractmethod
    # def _deval(self, qe, xi):
    #     """Compute
    #         * r_OP
    #         * A_IK
    #         * K_Gamma_bar
    #         * K_Kappa_bar
    #         * r_OP_qe
    #         * A_IK_qe
    #         * K_Gamma_bar_qe
    #         * K_Kappa_bar_qe
    #     """
    #     ...

    @abstractmethod
    def A_IK(self, t, q, frame_ID):
        ...

    @abstractmethod
    def A_IK_q(self, t, q, frame_ID):
        ...

    ################################################
    # constraint equations without constraint forces
    ################################################
    def g_S(self, t, q):
        g_S = np.zeros(self.nla_S, dtype=q.dtype)
        for node in range(self.nnodes_p):
            p = q[self.nodalDOF_p[node]]
            g_S[self.nodalDOF_la_S[node]] = np.array([p @ p - 1])
        return g_S

    def g_S_q(self, t, q):
        coo = CooMatrix((self.nla_S, self.nq))
        for node in range(self.nnodes_p):
            nodalDOF = self.nodalDOF_p[node]
            nodalDOF_S = self.nodalDOF_la_S[node]
            p = q[nodalDOF]
            coo[nodalDOF_S, nodalDOF] = 2 * p.reshape(1, -1)
        return coo

    def g_S_q_T_mu_q(self, t, q, mu):
        coo = CooMatrix((self.nq, self.nq))
        for node in range(self.nnodes_p):
            nodalDOF = self.nodalDOF_p[node]
            nodalDOF_S = self.nodalDOF_la_S[node]
            coo[nodalDOF, nodalDOF] = 2 * mu[node] * np.eye(4)
        return coo

    #########################################
    # kinematic equation
    #########################################
    def q_dot(self, t, q, u):
        # centerline part
        q_dot = np.zeros_like(q, dtype=np.common_type(q, u))

        # centerline part
        for node in range(self.nnodes_r):
            nodalDOF_r = self.nodalDOF_r[node]
            q_dot[nodalDOF_r] = u[nodalDOF_r]

        # axis angle vector part
        for node in range(self.nnodes_p):
            nodalDOF_p = self.nodalDOF_p[node]
            nodalDOF_p_u = self.nodalDOF_p_u[node]
            p = q[nodalDOF_p]
            K_omega_IK = u[nodalDOF_p_u]
            q_dot[nodalDOF_p] = T_SO3_inv_quat(p, normalize=False) @ K_omega_IK

        return q_dot

    def q_dot_u(self, t, q):
        coo = CooMatrix((self.nq, self.nu))

        # trivial kinematic equation for centerline
        coo[range(self.nq_r), range(self.nu_r)] = np.eye(self.nq_r)

        # axis angle vector part
        for node in range(self.nnodes_p):
            nodalDOF_p = self.nodalDOF_p[node]
            nodalDOF_p_u = self.nodalDOF_p_u[node]

            p = q[nodalDOF_p]
            p = p / norm(p)
            coo[nodalDOF_p, nodalDOF_p_u] = T_SO3_inv_quat(p, normalize=False)

        return coo

    def q_dot_q(self, t, q, u):
        coo = CooMatrix((self.nq, self.nq))

        # axis angle vector part
        for node in range(self.nnodes_p):
            nodalDOF_p = self.nodalDOF_p[node]
            nodalDOF_p_u = self.nodalDOF_p_u[node]
            p = q[nodalDOF_p]
            K_omega_IK = u[nodalDOF_p_u]

            coo[nodalDOF_p, nodalDOF_p] = np.einsum(
                "ijk,j->ik",
                T_SO3_inv_quat_P(p, normalize=False),
                K_omega_IK,
            )

        return coo

    def step_callback(self, t, q, u):
        for node in range(self.nnodes_p):
            p = q[self.nodalDOF_p[node]]
            q[self.nodalDOF_p[node]] = p / norm(p)
        return q, u

    ###############################
    # potential and internal forces
    ###############################
    def E_pot(self, t, q):
        E_pot = 0.0
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            E_pot += self.E_pot_el(q[elDOF], el)
        return E_pot

    def E_pot_el(self, qe, el):
        E_pot_el = 0.0

        for i in range(self.nquadrature):
            # extract reference state variables
            qpi = self.qp[el, i]
            qwi = self.qw[el, i]
            Ji = self.J[el, i]
            K_Gamma0 = self.K_Gamma0[el, i]
            K_Kappa0 = self.K_Kappa0[el, i]

            # evaluate required quantities
            _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

            # axial and shear strains
            K_Gamma = K_Gamma_bar / Ji

            # torsional and flexural strains
            K_Kappa = K_Kappa_bar / Ji

            # evaluate strain energy function
            E_pot_el += (
                self.material_model.potential(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)
                * Ji
                * qwi
            )

        return E_pot_el

    def eval_stresses(self, t, q, la_c, xi):
        el = self.element_number(xi)
        Qe = self.Q[self.elDOF[el]]
        qe = q[self.elDOF[el]]

        _, _, K_Gamma_bar0, K_Kappa_bar0 = self._eval(Qe, xi)

        J = norm(K_Gamma_bar0)
        K_Gamma0 = K_Gamma_bar0 / J
        K_Kappa0 = K_Kappa_bar0 / J

        _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, xi)

        K_Gamma = K_Gamma_bar / J
        K_Kappa = K_Kappa_bar / J

        K_n = self.material_model.K_n(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)
        K_m = self.material_model.K_m(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)

        return K_n, K_m

    def eval_strains(self, t, q, la_c, xi):
        el = self.element_number(xi)
        Qe = self.Q[self.elDOF[el]]
        qe = q[self.elDOF[el]]

        _, _, K_Gamma_bar0, K_Kappa_bar0 = self._eval(Qe, xi)

        J = norm(K_Gamma_bar0)
        K_Gamma0 = K_Gamma_bar0 / J
        K_Kappa0 = K_Kappa_bar0 / J

        _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, xi)

        K_Gamma = K_Gamma_bar / J
        K_Kappa = K_Kappa_bar / J

        return K_Gamma - K_Gamma0, K_Kappa - K_Kappa0

    #########################################
    # equations of motion
    #########################################
    def assembler_callback(self):
        self._M_coo()

    def M_el(self, el):
        M_el = np.zeros((self.nu_element, self.nu_element), dtype=float)

        for i in range(self.nquadrature_dyn):
            # extract reference state variables
            qwi = self.qw_dyn[el, i]
            Ji = self.J_dyn[el, i]

            # delta_r A_rho0 r_ddot part
            M_el_r_r = np.eye(3) * self.cross_section_inertias.A_rho0 * Ji * qwi
            for node_a in range(self.nnodes_element_r):
                nodalDOF_a = self.nodalDOF_element_r[node_a]
                for node_b in range(self.nnodes_element_r):
                    nodalDOF_b = self.nodalDOF_element_r[node_b]
                    M_el[nodalDOF_a[:, None], nodalDOF_b] += M_el_r_r * (
                        self.N_r_dyn[el, i, node_a] * self.N_r_dyn[el, i, node_b]
                    )

            # first part delta_phi (I_rho0 omega_dot + omega_tilde I_rho0 omega)
            M_el_p_p = self.cross_section_inertias.K_I_rho0 * Ji * qwi
            for node_a in range(self.nnodes_element_p):
                nodalDOF_a = self.nodalDOF_element_p_u[node_a]
                for node_b in range(self.nnodes_element_p):
                    nodalDOF_b = self.nodalDOF_element_p_u[node_b]
                    M_el[nodalDOF_a[:, None], nodalDOF_b] += M_el_p_p * (
                        self.N_p_dyn[el, i, node_a] * self.N_p_dyn[el, i, node_b]
                    )

        return M_el

    def _M_coo(self):
        self.__M = CooMatrix((self.nu, self.nu))
        for el in range(self.nelement):
            # extract element degrees of freedom
            elDOF_u = self.elDOF_u[el]

            # sparse assemble element mass matrix
            self.__M[elDOF_u, elDOF_u] = self.M_el(el)

    def M(self, t, q):
        return self.__M

    def E_kin(self, t, q, u):
        E_kin = 0.0
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            qe = q[elDOF]
            ue = u[elDOF_u]

            E_kin += self.E_kin_el(qe, ue, el)

        return E_kin

    def E_kin_el(self, qe, ue, el):
        E_kin_el = 0.0

        for i in range(self.nquadrature_dyn):
            # extract reference state variables
            qwi = self.qw_dyn[el, i]
            Ji = self.J_dyn[el, i]

            v_P = np.zeros(3, dtype=float)
            for node in range(self.nnodes_element_r):
                v_P += self.N_r_dyn[el, i, node] * ue[self.nodalDOF_element_r_u[node]]

            K_omega_IK = np.zeros(3, dtype=float)
            for node in range(self.nnodes_element_p):
                K_omega_IK += (
                    self.N_p_dyn[el, i, node] * ue[self.nodalDOF_element_p_u[node]]
                )

            # delta_r A_rho0 r_ddot part
            E_kin_el += (
                0.5 * (v_P @ v_P) * self.cross_section_inertias.A_rho0 * Ji * qwi
            )

            # first part delta_phi (I_rho0 omega_dot + omega_tilde I_rho0 omega)
            E_kin_el += (
                0.5
                * (K_omega_IK @ self.cross_section_inertias.K_I_rho0 @ K_omega_IK)
                * Ji
                * qwi
            )

        # E_kin_el = ue @ self.M_el_constant(el) @ ue

        return E_kin_el

    def linear_momentum(self, t, q, u):
        linear_momentum = np.zeros(3, dtype=float)
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            qe = q[elDOF]
            ue = u[elDOF_u]

            linear_momentum += self.linear_momentum_el(qe, ue, el)

        return linear_momentum

    def linear_momentum_el(self, qe, ue, el):
        linear_momentum_el = np.zeros(3, dtype=float)

        for i in range(self.nquadrature_dyn):
            # extract reference state variables
            qwi = self.qw_dyn[el, i]
            Ji = self.J_dyn[el, i]

            v_P = np.zeros(3, dtype=float)
            for node in range(self.nnodes_element_r):
                v_P += self.N_r_dyn[el, i, node] * ue[self.nodalDOF_element_r[node]]

            linear_momentum_el += v_P * self.cross_section_inertias.A_rho0 * Ji * qwi

        return linear_momentum_el

    def angular_momentum(self, t, q, u):
        angular_momentum = np.zeros(3, dtype=float)
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            qe = q[elDOF]
            ue = u[elDOF_u]

            angular_momentum += self.angular_momentum_el(t, qe, ue, el)

        return angular_momentum

    def angular_momentum_el(self, t, qe, ue, el):
        angular_momentum_el = np.zeros(3, dtype=float)

        for i in range(self.nquadrature_dyn):
            # extract reference state variables
            qpi = self.qp_dyn[el, i]
            qwi = self.qw_dyn[el, i]
            Ji = self.J_dyn[el, i]

            r_OP = np.zeros(3, dtype=float)
            v_P = np.zeros(3, dtype=float)
            for node in range(self.nnodes_element_r):
                r_OP += self.N_r_dyn[el, i, node] * qe[self.nodalDOF_element_r[node]]
                v_P += self.N_r_dyn[el, i, node] * ue[self.nodalDOF_element_r_u[node]]

            K_omega_IK = np.zeros(3, dtype=float)
            for node in range(self.nnodes_element_p):
                K_omega_IK += (
                    self.N_p_dyn[el, i, node] * ue[self.nodalDOF_element_p_u[node]]
                )

            A_IK = self.A_IK(t, qe, (qpi,))

            angular_momentum_el += (
                (
                    cross3(r_OP, v_P) * self.cross_section_inertias.A_rho0
                    + A_IK @ (self.cross_section_inertias.K_I_rho0 @ K_omega_IK)
                )
                * Ji
                * qwi
            )

        return angular_momentum_el

    def f_gyr_el(self, t, qe, ue, el):
        f_gyr_el = np.zeros(self.nu_element, dtype=np.common_type(qe, ue))

        for i in range(self.nquadrature_dyn):
            # interpoalte angular velocity
            K_Omega = np.zeros(3, dtype=np.common_type(qe, ue))
            for node in range(self.nnodes_element_p):
                K_Omega += (
                    self.N_p_dyn[el, i, node] * ue[self.nodalDOF_element_p_u[node]]
                )

            # vector of gyroscopic forces
            f_gyr_el_p = (
                cross3(K_Omega, self.cross_section_inertias.K_I_rho0 @ K_Omega)
                * self.J_dyn[el, i]
                * self.qw_dyn[el, i]
            )

            # multiply vector of gyroscopic forces with nodal virtual rotations
            for node in range(self.nnodes_element_p):
                f_gyr_el[self.nodalDOF_element_p_u[node]] += (
                    self.N_p_dyn[el, i, node] * f_gyr_el_p
                )

        return f_gyr_el

    def f_gyr_u_el(self, t, qe, ue, el):
        f_gyr_u_el = np.zeros((self.nu_element, self.nu_element), dtype=float)

        for i in range(self.nquadrature_dyn):
            # interpoalte angular velocity
            K_Omega = np.zeros(3, dtype=float)
            for node in range(self.nnodes_element_p):
                K_Omega += (
                    self.N_p_dyn[el, i, node] * ue[self.nodalDOF_element_p_u[node]]
                )

            # derivative of vector of gyroscopic forces
            f_gyr_u_el_p = (
                (
                    (
                        ax2skew(K_Omega) @ self.cross_section_inertias.K_I_rho0
                        - ax2skew(self.cross_section_inertias.K_I_rho0 @ K_Omega)
                    )
                )
                * self.J_dyn[el, i]
                * self.qw_dyn[el, i]
            )

            # multiply derivative of gyroscopic force vector with nodal virtual rotations
            for node_a in range(self.nnodes_element_p):
                nodalDOF_a = self.nodalDOF_element_p_u[node_a]
                for node_b in range(self.nnodes_element_p):
                    nodalDOF_b = self.nodalDOF_element_p_u[node_b]
                    f_gyr_u_el[nodalDOF_a[:, None], nodalDOF_b] += f_gyr_u_el_p * (
                        self.N_p_dyn[el, i, node_a] * self.N_p_dyn[el, i, node_b]
                    )

        return f_gyr_u_el

    def f_pot_el(self, qe, el):
        f_pot_el = np.zeros(self.nu_element, dtype=qe.dtype)

        for i in range(self.nquadrature):
            # extract reference state variables
            qpi = self.qp[el, i]
            qwi = self.qw[el, i]
            J = self.J[el, i]
            K_Gamma0 = self.K_Gamma0[el, i]
            K_Kappa0 = self.K_Kappa0[el, i]

            # evaluate required quantities
            _, A_IK, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

            # axial and shear strains
            K_Gamma = K_Gamma_bar / J

            # torsional and flexural strains
            K_Kappa = K_Kappa_bar / J

            # compute contact forces and couples from partial derivatives of
            # the strain energy function w.r.t. strain measures
            K_n = self.material_model.K_n(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)
            K_m = self.material_model.K_m(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)

            ############################
            # virtual work contributions
            ############################
            for node in range(self.nnodes_element_r):
                f_pot_el[self.nodalDOF_element_r_u[node]] -= (
                    self.N_r_xi[el, i, node] * A_IK @ K_n * qwi
                )

            for node in range(self.nnodes_element_p):
                f_pot_el[self.nodalDOF_element_p_u[node]] -= (
                    self.N_p_xi[el, i, node] * K_m * qwi
                )

                f_pot_el[self.nodalDOF_element_p_u[node]] += (
                    self.N_p[el, i, node]
                    * (cross3(K_Gamma_bar, K_n) + cross3(K_Kappa_bar, K_m))
                    * qwi
                )

        return f_pot_el

    def f_pot_el_q(self, qe, el):
        if not hasattr(self, "_deval"):
            warnings.warn(
                "Class derived from CosseratRod does not implement _deval. We use a numerical Jacobian!"
            )
            return approx_fprime(
                qe, lambda qe: self.f_pot_el(qe, el), method="3-point", eps=1e-6
            )
        else:
            f_pot_q_el = np.zeros((self.nu_element, self.nq_element), dtype=float)

            for i in range(self.nquadrature):
                # extract reference state variables
                qpi = self.qp[el, i]
                qwi = self.qw[el, i]
                J = self.J[el, i]
                K_Gamma0 = self.K_Gamma0[el, i]
                K_Kappa0 = self.K_Kappa0[el, i]

                # evaluate required quantities
                (
                    r_OP,
                    A_IK,
                    K_Gamma_bar,
                    K_Kappa_bar,
                    r_OP_qe,
                    A_IK_qe,
                    K_Gamma_bar_qe,
                    K_Kappa_bar_qe,
                ) = self._deval(qe, qpi)

                # axial and shear strains
                K_Gamma = K_Gamma_bar / J
                K_Gamma_qe = K_Gamma_bar_qe / J

                # torsional and flexural strains
                K_Kappa = K_Kappa_bar / J
                K_Kappa_qe = K_Kappa_bar_qe / J

                # compute contact forces and couples from partial derivatives of
                # the strain energy function w.r.t. strain measures
                K_n = self.material_model.K_n(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)
                K_n_K_Gamma = self.material_model.K_n_K_Gamma(
                    K_Gamma, K_Gamma0, K_Kappa, K_Kappa0
                )
                K_n_K_Kappa = self.material_model.K_n_K_Kappa(
                    K_Gamma, K_Gamma0, K_Kappa, K_Kappa0
                )
                K_n_qe = K_n_K_Gamma @ K_Gamma_qe + K_n_K_Kappa @ K_Kappa_qe

                K_m = self.material_model.K_m(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)
                K_m_K_Gamma = self.material_model.K_m_K_Gamma(
                    K_Gamma, K_Gamma0, K_Kappa, K_Kappa0
                )
                K_m_K_Kappa = self.material_model.K_m_K_Kappa(
                    K_Gamma, K_Gamma0, K_Kappa, K_Kappa0
                )
                K_m_qe = K_m_K_Gamma @ K_Gamma_qe + K_m_K_Kappa @ K_Kappa_qe

                ############################
                # virtual work contributions
                ############################
                for node in range(self.nnodes_element_r):
                    f_pot_q_el[self.nodalDOF_element_r[node], :] -= (
                        self.N_r_xi[el, i, node]
                        * qwi
                        * (np.einsum("ikj,k->ij", A_IK_qe, K_n) + A_IK @ K_n_qe)
                    )

                for node in range(self.nnodes_element_p):
                    f_pot_q_el[self.nodalDOF_element_p_u[node], :] += (
                        self.N_p[el, i, node]
                        * qwi
                        * (
                            ax2skew(K_Gamma_bar) @ K_n_qe
                            - ax2skew(K_n) @ K_Gamma_bar_qe
                        )
                    )

                    f_pot_q_el[self.nodalDOF_element_p_u[node], :] -= (
                        self.N_p_xi[el, i, node] * qwi * K_m_qe
                    )

                    f_pot_q_el[self.nodalDOF_element_p_u[node], :] += (
                        self.N_p[el, i, node]
                        * qwi
                        * (
                            ax2skew(K_Kappa_bar) @ K_m_qe
                            - ax2skew(K_m) @ K_Kappa_bar_qe
                        )
                    )

            return f_pot_q_el

            # f_pot_q_el_num = approx_fprime(
            #     qe, lambda qe: self.f_pot_el(qe, el), eps=1.0e-10, method="cs"
            # )
            # # f_pot_q_el_num = approx_fprime(
            # #     qe, lambda qe: self.f_pot_el(qe, el), eps=5.0e-6, method="2-point"
            # # )
            # diff = f_pot_q_el - f_pot_q_el_num
            # error = np.linalg.norm(diff)
            # print(f"error f_pot_q_el: {error}")
            # return f_pot_q_el_num

    def h(self, t, q, u):
        h = np.zeros(self.nu, dtype=np.common_type(q, u))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            h[elDOF_u] += self.f_pot_el(q[elDOF], el)
        return h

    def h_q(self, t, q, u):
        coo = CooMatrix((self.nu, self.nq))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            coo[elDOF_u, elDOF] = self.f_pot_el_q(q[elDOF], el)
        return coo

    def h_u(self, t, q, u):
        coo = CooMatrix((self.nu, self.nu))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            coo[elDOF_u, elDOF_u] = -self.f_gyr_u_el(t, q[elDOF], u[elDOF_u], el)
        return coo

    ####################################################
    # interactions with other bodies and the environment
    ####################################################
    def elDOF_P(self, frame_ID):
        xi = frame_ID[0]
        el = self.element_number(xi)
        return self.elDOF[el]

    def elDOF_P_u(self, frame_ID):
        xi = frame_ID[0]
        el = self.element_number(xi)
        return self.elDOF_u[el]

    def local_qDOF_P(self, frame_ID):
        return self.elDOF_P(frame_ID)

    def local_uDOF_P(self, frame_ID):
        return self.elDOF_P_u(frame_ID)

    #########################
    # r_OP/ A_IK contribution
    #########################
    def r_OP(self, t, q, frame_ID, K_r_SP=np.zeros(3, dtype=float)):
        # TODO: correct this function _eval need to qe insteaf of q
        r_OP, A_IK, _, _ = self._eval(q, frame_ID[0])
        return r_OP + A_IK @ K_r_SP

    # TODO: Think of a faster version than using _deval
    def r_OP_q(self, t, q, frame_ID, K_r_SP=np.zeros(3, dtype=float)):
        if not hasattr(self, "_deval"):
            warnings.warn(
                "Class derived from TimoshenkoPetrovGalerkinBase does not implement _deval. We use a numerical Jacobian!"
            )
            return approx_fprime(
                q,
                lambda q: self.r_OP(t, q, frame_ID=frame_ID, K_r_SP=K_r_SP),
                # method="cs",
                # eps=1.0e-12,
                method="3-point",
                eps=1e-6,
            )
        else:
            # evaluate required quantities
            (
                r_OP,
                A_IK,
                _,
                _,
                r_OP_q,
                A_IK_q,
                _,
                _,
            ) = self._deval(q, frame_ID[0])

        return r_OP_q + np.einsum("ijk,j->ik", A_IK_q, K_r_SP)

    def v_P(self, t, q, u, frame_ID, K_r_SP=np.zeros(3, dtype=float)):
        N_r, _ = self.basis_functions_r(frame_ID[0])
        N_p, _ = self.basis_functions_p(frame_ID[0])

        # interpolate A_IK and angular velocity in K-frame
        A_IK = self.A_IK(t, q, frame_ID)
        K_Omega = np.zeros(3, dtype=np.common_type(q, u))
        for node in range(self.nnodes_element_p):
            K_Omega += N_p[node] * u[self.nodalDOF_element_p_u[node]]

        # centerline velocity
        v = np.zeros(3, dtype=np.common_type(q, u))
        for node in range(self.nnodes_element_r):
            v += N_r[node] * u[self.nodalDOF_element_r[node]]

        return v + A_IK @ cross3(K_Omega, K_r_SP)

    def v_P_q(self, t, q, u, frame_ID, K_r_SP=np.zeros(3, dtype=float)):
        # evaluate shape functions
        N_p, _ = self.basis_functions_p(frame_ID[0])

        # interpolate derivative of A_IK and angular velocity in K-frame
        A_IK_q = self.A_IK_q(t, q, frame_ID)
        K_Omega = np.zeros(3, dtype=np.common_type(q, u))
        for node in range(self.nnodes_element_p):
            K_Omega += N_p[node] * u[self.nodalDOF_element_p_u[node]]

        v_P_q = np.einsum(
            "ijk,j->ik",
            A_IK_q,
            cross3(K_Omega, K_r_SP),
        )
        return v_P_q

        # v_P_q_num = approx_fprime(
        #     q, lambda q: self.v_P(t, q, u, frame_ID, K_r_SP), method="3-point"
        # )
        # diff = v_P_q - v_P_q_num
        # error = np.linalg.norm(diff)
        # print(f"error v_P_q: {error}")
        # return v_P_q_num

    def J_P(self, t, q, frame_ID, K_r_SP=np.zeros(3, dtype=float)):
        # evaluate required nodal shape functions
        N_r, _ = self.basis_functions_r(frame_ID[0])
        N_p, _ = self.basis_functions_p(frame_ID[0])

        # transformation matrix
        A_IK = self.A_IK(t, q, frame_ID)

        # skew symmetric matrix of K_r_SP
        K_r_SP_tilde = ax2skew(K_r_SP)

        # interpolate centerline and axis angle contributions
        J_P = np.zeros((3, self.nu_element), dtype=q.dtype)
        for node in range(self.nnodes_element_r):
            J_P[:, self.nodalDOF_element_r[node]] += N_r[node] * np.eye(3)
        for node in range(self.nnodes_element_p):
            J_P[:, self.nodalDOF_element_p_u[node]] -= (
                N_p[node] * A_IK @ K_r_SP_tilde
            )

        return J_P

        # J_P_num = approx_fprime(
        #     np.zeros(self.nq_element, dtype=float),
        #     lambda u: self.v_P(t, q, u, frame_ID, K_r_SP),
        # )
        # diff = J_P_num - J_P
        # error = np.linalg.norm(diff)
        # print(f"error J_P: {error}")
        # return J_P_num

    def J_P_q(self, t, q, frame_ID, K_r_SP=np.zeros(3, dtype=float)):
        # evaluate required nodal shape functions
        N_p, _ = self.basis_functions_p(frame_ID[0])

        K_r_SP_tilde = ax2skew(K_r_SP)
        A_IK_q = self.A_IK_q(t, q, frame_ID)
        prod = np.einsum("ijl,jk", A_IK_q, K_r_SP_tilde)

        # interpolate axis angle contributions since centerline contributon is
        # zero
        J_P_q = np.zeros((3, self.nu_element, self.nq_element), dtype=float)
        for node in range(self.nnodes_element_p):
            nodalDOF_p_u = self.nodalDOF_element_p_u[node]
            J_P_q[:, nodalDOF_p_u] -= N_p[node] * prod

        return J_P_q

        # J_P_q_num = approx_fprime(
        #     q, lambda q: self.J_P(t, q, frame_ID, K_r_SP), method="3-point"
        # )
        # diff = J_P_q_num - J_P_q
        # error = np.linalg.norm(diff)
        # print(f"error J_P_q: {error}")
        # return J_P_q_num

    def a_P(self, t, q, u, u_dot, frame_ID, K_r_SP=np.zeros(3, dtype=float)):
        N_r, _ = self.basis_functions_r(frame_ID[0])

        # interpolate orientation
        A_IK = self.A_IK(t, q, frame_ID)

        # centerline acceleration
        a = np.zeros(3, dtype=np.common_type(q, u, u_dot))
        for node in range(self.nnodes_element_r):
            a += N_r[node] * u_dot[self.nodalDOF_element_r[node]]

        # angular velocity and acceleration in K-frame
        K_Omega = self.K_Omega(t, q, u, frame_ID)
        K_p = self.K_p(t, q, u, u_dot, frame_ID)

        # rigid body formular
        return a + A_IK @ (
            cross3(K_p, K_r_SP) + cross3(K_Omega, cross3(K_Omega, K_r_SP))
        )

    def a_P_q(self, t, q, u, u_dot, frame_ID, K_r_SP=None):
        K_Omega = self.K_Omega(t, q, u, frame_ID)
        K_p = self.K_p(t, q, u, u_dot, frame_ID)
        a_P_q = np.einsum(
            "ijk,j->ik",
            self.A_IK_q(t, q, frame_ID),
            cross3(K_p, K_r_SP) + cross3(K_Omega, cross3(K_Omega, K_r_SP)),
        )
        return a_P_q

        # a_P_q_num = approx_fprime(
        #     q,
        #     lambda q: self.a_P(t, q, u, u_dot, frame_ID, K_r_SP),
        #     method="3-point",
        # )
        # diff = a_P_q_num - a_P_q
        # error = np.linalg.norm(diff)
        # print(f"error a_P_q: {error}")
        # return a_P_q_num

    def a_P_u(self, t, q, u, u_dot, frame_ID, K_r_SP=None):
        K_Omega = self.K_Omega(t, q, u, frame_ID)
        local = -self.A_IK(t, q, frame_ID) @ (
            ax2skew(cross3(K_Omega, K_r_SP)) + ax2skew(K_Omega) @ ax2skew(K_r_SP)
        )

        N, _ = self.basis_functions_r(frame_ID[0])
        a_P_u = np.zeros((3, self.nu_element), dtype=float)
        for node in range(self.nnodes_element_r):
            a_P_u[:, self.nodalDOF_element_p_u[node]] += N[node] * local

        return a_P_u

        # a_P_u_num = approx_fprime(
        #     u,
        #     lambda u: self.a_P(t, q, u, u_dot, frame_ID, K_r_SP),
        #     method="3-point",
        # )
        # diff = a_P_u_num - a_P_u
        # error = np.linalg.norm(diff)
        # print(f"error a_P_u: {error}")
        # return a_P_u_num

    def K_Omega(self, t, q, u, frame_ID):
        """Since we use Petrov-Galerkin method we only interpoalte the nodal
        angular velocities in the K-frame.
        """
        N_p, _ = self.basis_functions_p(frame_ID[0])
        K_Omega = np.zeros(3, dtype=np.common_type(q, u))
        for node in range(self.nnodes_element_p):
            K_Omega += N_p[node] * u[self.nodalDOF_element_p_u[node]]
        return K_Omega

    def K_Omega_q(self, t, q, u, frame_ID):
        return np.zeros((3, self.nq_element), dtype=float)

    def K_J_R(self, t, q, frame_ID):
        N_p, _ = self.basis_functions_p(frame_ID[0])
        K_J_R = np.zeros((3, self.nu_element), dtype=float)
        for node in range(self.nnodes_element_p):
            K_J_R[:, self.nodalDOF_element_p_u[node]] += N_p[node] * np.eye(
                3, dtype=float
            )
        return K_J_R

    def K_J_R_q(self, t, q, frame_ID):
        return np.zeros((3, self.nu_element, self.nq_element), dtype=float)

    def K_p(self, t, q, u, u_dot, frame_ID):
        """Since we use Petrov-Galerkin method we only interpoalte the nodal
        time derivative of the angular velocities in the K-frame.
        """
        N_p, _ = self.basis_functions_p(frame_ID[0])
        K_p = np.zeros(3, dtype=np.common_type(q, u, u_dot))
        for node in range(self.nnodes_element_p):
            K_p += N_p[node] * u_dot[self.nodalDOF_element_p_u[node]]
        return K_p

    def K_p_q(self, t, q, u, u_dot, frame_ID):
        return np.zeros((3, self.nq_element), dtype=float)

    def K_p_u(self, t, q, u, u_dot, frame_ID):
        return np.zeros((3, self.nu_element), dtype=float)

    ####################################################
    # body force
    ####################################################
    def distributed_force1D_pot_el(self, force, t, qe, el):
        Ve = 0.0

        # # TODO: We should use self.nquadrature_dyn!
        # for i in range(self.nquadrature):
        #     # extract reference state variables
        #     qpi = self.qp[el, i]
        #     qwi = self.qw[el, i]
        #     Ji = self.J[el, i]

        #     # interpolate centerline position
        #     r_C = np.zeros(3, dtype=float)
        #     for node in range(self.nnodes_element_r):
        #         r_C += self.N_r[el, i, node] * qe[self.nodalDOF_element_r[node]]

        #     # compute potential value at given quadrature point
        #     Ve -= (r_C @ force(t, qpi)) * Ji * qwi

        for i in range(self.nquadrature_dyn):
            # extract reference state variables
            qpi = self.qp_dyn[el, i]
            qwi = self.qw_dyn[el, i]
            Ji = self.J_dyn[el, i]

            # interpolate centerline position
            r_C = np.zeros(3, dtype=float)
            for node in range(self.nnodes_element_r):
                r_C += self.N_r_dyn[el, i, node] * qe[self.nodalDOF_element_r[node]]

            # compute potential value at given quadrature point
            Ve -= (r_C @ force(t, qpi)) * Ji * qwi

        return Ve

    def distributed_force1D_pot(self, t, q, force):
        V = 0
        for el in range(self.nelement):
            qe = q[self.elDOF[el]]
            V += self.distributed_force1D_pot_el(force, t, qe, el)
        return V

    # TODO: Decide which number of quadrature points should be used here?
    def distributed_force1D_el(self, force, t, el):
        fe = np.zeros(self.nu_element, dtype=float)

        # for i in range(self.nquadrature):
        #     # extract reference state variables
        #     qpi = self.qp[el, i]
        #     qwi = self.qw[el, i]
        #     Ji = self.J[el, i]

        #     # compute local force vector
        #     fe_r = force(t, qpi) * Ji * qwi

        #     # multiply local force vector with variation of centerline
        #     for node in range(self.nnodes_element_r):
        #         fe[self.nodalDOF_element_r[node]] += self.N_r[el, i, node] * fe_r

        for i in range(self.nquadrature_dyn):
            # extract reference state variables
            qpi = self.qp_dyn[el, i]
            qwi = self.qw_dyn[el, i]
            Ji = self.J_dyn[el, i]

            # compute local force vector
            fe_r = force(t, qpi) * Ji * qwi

            # multiply local force vector with variation of centerline
            for node in range(self.nnodes_element_r):
                fe[self.nodalDOF_element_r[node]] += self.N_r_dyn[el, i, node] * fe_r

        return fe

    def distributed_force1D(self, t, q, force):
        f = np.zeros(self.nu, dtype=float)
        for el in range(self.nelement):
            f[self.elDOF_u[el]] += self.distributed_force1D_el(force, t, el)
        return f

    def distributed_force1D_q(self, t, q, force):
        return None


class CosseratRodMixed(CosseratRod):
    def __init__(
        self,
        cross_section,
        material_model,
        nelement,
        polynomial_degree,
        nquadrature,
        Q,
        q0=None,
        u0=None,
        nquadrature_dyn=None,
        cross_section_inertias=CrossSectionInertias(),
        idx_mixed=np.arange(6),
    ):
        """Base class for Petrov-Galerkin Cosserat rod formulations that uses
        quaternions for the parametrization of the nodal orientations."""

        super().__init__(
            cross_section,
            material_model,
            nelement,
            polynomial_degree,
            nquadrature,
            Q,
            q0=q0,
            u0=u0,
            nquadrature_dyn=nquadrature_dyn,
            cross_section_inertias=cross_section_inertias,
        )

        #######################################################
        # discretization parameters internal forces and moments
        #######################################################
        self.idx_mixed = np.array(idx_mixed)
        self.idx_n = self.idx_mixed[(self.idx_mixed < 3)]
        self.nmixed_n = len(self.idx_n)
        self.K_n_la_c_sieve = np.zeros((3, self.nmixed_n))
        for i, ni in enumerate(self.idx_n):
            self.K_n_la_c_sieve[ni, i] = 1

        self.idx_m = self.idx_mixed[(self.idx_mixed >= 3)] - 3
        self.nmixed_m = len(self.idx_m)
        self.K_m_la_c_sieve = np.zeros((3, self.nmixed_m))
        for i, mi in enumerate(self.idx_m):
            self.K_m_la_c_sieve[mi, i] = 1

        self.polynomial_degree_la_c = polynomial_degree - 1
        self.knot_vector_la_c = LagrangeKnotVector(
            self.polynomial_degree_la_c, nelement
        )

        # build mesh objects
        self.mesh_la_c = Mesh1D(
            self.knot_vector_la_c,
            nquadrature,
            dim_q=len(self.idx_mixed),
            derivative_order=0,
            basis="Lagrange_Disc",
            quadrature="Gauss",
            dim_u=len(self.idx_mixed),
        )

        # total number of nodes
        self.nnodes_la_c = self.mesh_la_c.nnodes

        # number of nodes per element
        self.nnodes_element_la_c = self.mesh_la_c.nnodes_per_element

        # total number of compliance coordinates
        self.nla_c = self.mesh_la_c.nq

        # number of compliance coordinates per element
        self.nla_c_element = self.mesh_la_c.nq_per_element

        # global element connectivity for copliance coordinates
        self.elDOF_la_c = self.mesh_la_c.elDOF

        # global nodal connectivity
        self.nodalDOF_la_c = self.mesh_la_c.nodalDOF + self.nq_r + self.nq_p

        # nodal connectivity on element level
        self.nodalDOF_element_la_c = self.mesh_la_c.nodalDOF_element

        # shape functions and their first derivatives
        self.N_la_c = self.mesh_la_c.N

        # TODO: Dummy initial values for compliance
        self.la_c0 = np.zeros(self.nla_c, dtype=float)

        # evaluate shape functions at specific xi
        self.basis_functions_la_c = self.mesh_la_c.eval_basis

    ###############################
    # potential and internal forces
    ###############################
    # # TODO:
    # def E_pot(self, t, q):
    #     E_pot = 0.0
    #     for el in range(self.nelement):
    #         elDOF = self.elDOF[el]
    #         E_pot += self.E_pot_el(q[elDOF], el)
    #     return E_pot

    # # TODO:
    # def E_pot_el(self, qe, el):
    #     E_pot_el = 0.0

    #     for i in range(self.nquadrature):
    #         # extract reference state variables
    #         qpi = self.qp[el, i]
    #         qwi = self.qw[el, i]
    #         Ji = self.J[el, i]
    #         K_Gamma0 = self.K_Gamma0[el, i]
    #         K_Kappa0 = self.K_Kappa0[el, i]

    #         # evaluate required quantities
    #         _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

    #         # axial and shear strains
    #         K_Gamma = K_Gamma_bar / Ji

    #         # torsional and flexural strains
    #         K_Kappa = K_Kappa_bar / Ji

    #         # evaluate strain energy function
    #         E_pot_el += (
    #             self.material_model.potential(K_Gamma, K_Gamma0, K_Kappa, K_Kappa0)
    #             * Ji
    #             * qwi
    #         )

    #     return E_pot_el

    def eval_stresses(self, t, q, la_c, xi):
        el = self.element_number(xi)
        la_ce = la_c[self.elDOF_la_c[el]]
        # TODO: lets see how to avoid the flatten
        N_la_ce = self.basis_functions_la_c(xi).flatten()
        la_cc = np.zeros(len(self.idx_mixed))

        for node in range(self.nnodes_element_la_c):
            la_c_node = la_ce[self.nodalDOF_element_la_c[node]]
            la_cc += N_la_ce[node] * la_c_node

        K_n = self.K_n_la_c_sieve @ la_cc[: self.nmixed_n]
        K_m = self.K_m_la_c_sieve @ la_cc[self.nmixed_n :]

        return K_n, K_m

    def eval_strains(self, t, q, la_c, xi):
        K_n, K_m = self.eval_stresses(t, q, la_c, xi)
        el = self.element_number(xi)
        Qe = self.Q[self.elDOF[el]]

        _, _, K_Gamma_bar0, K_Kappa_bar0 = self._eval(Qe, xi)

        J = norm(K_Gamma_bar0)
        K_Gamma0 = K_Gamma_bar0 / J
        K_Kappa0 = K_Kappa_bar0 / J

        C_n_inv = self.material_model.C_n_inv
        C_m_inv = self.material_model.C_m_inv

        K_Gamma = C_n_inv @ K_n + K_Gamma0
        K_Kappa = C_m_inv @ K_m + K_Kappa0

        return K_Gamma - K_Gamma0, K_Kappa - K_Kappa0

    ##########################
    # compliance contributions
    ##########################
    # TODO: implement me
    def la_c(self, t, q, u):
        raise NotImplementedError

    def c(self, t, q, u, la_c):
        c = np.zeros(self.nla_c, dtype=np.common_type(q, u, la_c))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_la_c = self.elDOF_la_c[el]
            c[elDOF_la_c] = self.c_el(q[elDOF], la_c[elDOF_la_c], el)
        return c

    def c_el(self, qe, la_ce, el):
        c_el = np.zeros(self.nla_c_element, dtype=np.common_type(qe, la_ce))

        for i in range(self.nquadrature):
            # extract reference state variables
            qpi = self.qp[el, i]
            qwi = self.qw[el, i]
            J = self.J[el, i]
            K_Gamma0 = self.K_Gamma0[el, i]
            K_Kappa0 = self.K_Kappa0[el, i]

            # evaluate required quantities
            _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

            la_c = np.zeros(self.mesh_la_c.dim_q, dtype=la_ce.dtype)
            # interpolation of internal forces and moments
            for node in range(self.nnodes_element_la_c):
                la_c_node = la_ce[self.nodalDOF_element_la_c[node]]
                la_c += self.N_la_c[el, i, node] * la_c_node

            K_n = self.K_n_la_c_sieve @ la_c[: self.nmixed_n]
            K_m = self.K_m_la_c_sieve @ la_c[self.nmixed_n :]

            # compute contact forces and couples from partial derivatives of
            # the strain energy function w.r.t. strain measures
            C_n_inv = self.material_model.C_n_inv
            C_m_inv = self.material_model.C_m_inv

            # TODO: Store K_Gamma_bar0 and K_Kappa_bar0
            c_qp = np.concatenate(
                (
                    (J * C_n_inv @ K_n - (K_Gamma_bar - J * K_Gamma0)),
                    (J * C_m_inv @ K_m - (K_Kappa_bar - J * K_Kappa0)),
                )
            )

            for node in range(self.nnodes_element_la_c):
                c_el[self.nodalDOF_element_la_c[node]] += (
                    self.N_la_c[el, i, node] * c_qp[self.idx_mixed] * qwi
                )

        return c_el

    def c_la_c(self):
        coo = CooMatrix((self.nla_c, self.nla_c))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_la_c = self.elDOF_la_c[el]
            coo[elDOF_la_c, elDOF_la_c] = self.c_la_c_el(el)
        return coo

    def c_la_c_el(self, el):
        c_la_c_el = np.zeros((self.nla_c_element, self.nla_c_element))
        for i in range(self.nquadrature):
            qwi = self.qw[el, i]
            J = self.J[el, i]

            C_n_inv = self.material_model.C_n_inv
            C_m_inv = self.material_model.C_m_inv

            C_inv = np.block(
                [[C_n_inv, np.zeros_like(C_n_inv)], [np.zeros_like(C_m_inv), C_m_inv]]
            )

            C_inv_sliced = C_inv[self.idx_mixed[:, None], self.idx_mixed]

            for node_la_c1 in range(self.nnodes_element_la_c):
                nodalDOF_la_c1 = self.nodalDOF_element_la_c[node_la_c1]
                for node_la_c2 in range(self.nnodes_element_la_c):
                    nodalDOF_la_c2 = self.nodalDOF_element_la_c[node_la_c2]
                    c_la_c_el[nodalDOF_la_c1[:, None], nodalDOF_la_c2] += (
                        self.N_la_c[el, i, node_la_c1]
                        * C_inv_sliced
                        * self.N_la_c[el, i, node_la_c2]
                        * J
                        * qwi
                    )

        return c_la_c_el

    def c_q(self, t, q, u, la_c):
        coo = CooMatrix((self.nla_c, self.nq))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_la_c = self.elDOF_la_c[el]
            coo[elDOF_la_c, elDOF] = self.c_q_el(q[elDOF], la_c[elDOF_la_c], el)
        return coo

    def c_q_el(self, qe, la_ce, el):
        if not hasattr(self, "_deval"):
            warnings.warn(
                "Class derived from CosseratRod does not implement _deval. We use a numerical Jacobian!"
            )
            return approx_fprime(
                qe, lambda qe: self.c_el(qe, la_ce, el), method="3-point", eps=1e-6
            )

        c_q_el = np.zeros(
            (self.nla_c_element, self.nq_element), dtype=np.common_type(qe, la_ce)
        )
        for i in range(self.nquadrature):
            # extract reference state variables
            qpi = self.qp[el, i]
            qwi = self.qw[el, i]
            J = self.J[el, i]

            # evaluate required quantities
            (
                _,
                _,
                _,
                _,
                _,
                _,
                K_Gamma_bar_qe,
                K_Kappa_bar_qe,
            ) = self._deval(qe, qpi)

            delta_strains_qe = np.vstack((K_Gamma_bar_qe, K_Kappa_bar_qe))

            for node in range(self.nnodes_element_la_c):
                nodalDOF_la_c = self.nodalDOF_element_la_c[node]
                c_q_el[nodalDOF_la_c, :] -= (
                    self.N_la_c[el, i, node] * delta_strains_qe[self.idx_mixed, :] * qwi
                )

        return c_q_el

    def W_c(self, t, q):
        coo = CooMatrix((self.nu, self.nla_c))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            elDOF_la_c = self.elDOF_la_c[el]
            coo[elDOF_u, elDOF_la_c] = self.W_c_el(q[elDOF], el)
        return coo

    def W_c_el(self, qe, el):
        W_c_el = np.zeros((self.nu_element, self.nla_c_element), dtype=qe.dtype)

        for i in range(self.nquadrature):
            # extract reference state variables
            qpi = self.qp[el, i]
            qwi = self.qw[el, i]

            # evaluate required quantities
            _, A_IK, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

            ############################
            # virtual work contributions
            ############################
            for node_r in range(self.nnodes_element_r):
                nodalDOF_r = self.nodalDOF_element_r_u[node_r]
                N_r_xi = self.N_r_xi[el, i, node_r]
                for node_la_c in range(self.nnodes_element_la_c):
                    nodalDOF_la_c = self.nodalDOF_element_la_c[node_la_c]
                    W_c_el[nodalDOF_r[:, None], nodalDOF_la_c[: self.nmixed_n]] -= (
                        N_r_xi
                        * A_IK[:, self.idx_n]
                        * self.N_la_c[el, i, node_la_c]
                        * qwi
                    )

            for node_p in range(self.nnodes_element_p):
                nodalDOF_p = self.nodalDOF_element_p_u[node_p]
                N_p = self.N_p[el, i, node_p]
                N_p_xi = self.N_p_xi[el, i, node_p]

                for node_la_c in range(self.nnodes_element_la_c):
                    nodalDOF_la_c = self.nodalDOF_element_la_c[node_la_c]
                    W_c_el[nodalDOF_p[:, None], nodalDOF_la_c[self.nmixed_n :]] -= (
                        (N_p_xi * np.eye(3) - N_p * ax2skew(K_Kappa_bar))[
                            :, self.idx_m
                        ]
                        * self.N_la_c[el, i, node_la_c]
                        * qwi
                    )

                    W_c_el[nodalDOF_p[:, None], nodalDOF_la_c[: self.nmixed_n]] += (
                        N_p
                        * ax2skew(K_Gamma_bar)[:, self.idx_n]
                        * self.N_la_c[el, i, node_la_c]
                        * qwi
                    )

        return W_c_el

    def Wla_c_q(self, t, q, la_c):
        coo = CooMatrix((self.nu, self.nq))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            elDOF_la_c = self.elDOF_la_c[el]
            coo[elDOF_u, elDOF] = self.Wla_c_q_el(q[elDOF], la_c[elDOF_la_c], el)
        return coo

    def Wla_c_q_el(self, qe, la_ce, el):
        if not hasattr(self, "_deval"):
            warnings.warn(
                "Class derived from CosseratRod does not implement _deval. We use a numerical Jacobian!"
            )
            return approx_fprime(
                qe, lambda qe: self.W_c_el(qe, el) @ la_ce, method="3-point", eps=1e-6
            )

        Wla_c_q_el = np.zeros(
            (self.nu_element, self.nq_element), dtype=np.common_type(qe, la_ce)
        )

        for i in range(self.nquadrature):
            # extract reference state variables
            qpi = self.qp[el, i]
            qwi = self.qw[el, i]
            J = self.J[el, i]

            # evaluate required quantities
            (
                r_OP,
                A_IK,
                K_Gamma_bar,
                K_Kappa_bar,
                r_OP_qe,
                A_IK_qe,
                K_Gamma_bar_qe,
                K_Kappa_bar_qe,
            ) = self._deval(qe, qpi)

            # interpolation of the n and m
            la_c = np.zeros(self.mesh_la_c.dim_q, dtype=qe.dtype)

            for node in range(self.nnodes_element_la_c):
                la_c_node = la_ce[self.nodalDOF_element_la_c[node]]
                la_c += self.N_la_c[el, i, node] * la_c_node

            K_n = self.K_n_la_c_sieve @ la_c[: self.nmixed_n]
            K_m = self.K_m_la_c_sieve @ la_c[self.nmixed_n :]

            for node in range(self.nnodes_element_r):
                Wla_c_q_el[self.nodalDOF_element_r_u[node], :] -= (
                    self.N_r_xi[el, i, node]
                    * qwi
                    * (np.einsum("ikj,k->ij", A_IK_qe, K_n))
                )

            for node in range(self.nnodes_element_p):
                Wla_c_q_el[self.nodalDOF_element_p_u[node], :] += (
                    self.N_p[el, i, node]
                    * qwi
                    * (-ax2skew(K_n) @ K_Gamma_bar_qe - ax2skew(K_m) @ K_Kappa_bar_qe)
                )

        return Wla_c_q_el

    #########################################
    # equations of motion
    #########################################
    def h(self, t, q, u):
        h = np.zeros(self.nu, dtype=np.common_type(q, u))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            h[elDOF_u] -= self.f_gyr_el(t, q[elDOF], u[elDOF_u], el)
        return h

    def h_q(self, t, q, u):
        # h_q required to overwrite function of parent class
        coo = CooMatrix((self.nu, self.nq))
        return coo

    def h_u(self, t, q, u):
        coo = CooMatrix((self.nu, self.nu))
        for el in range(self.nelement):
            elDOF = self.elDOF[el]
            elDOF_u = self.elDOF_u[el]
            coo[elDOF_u, elDOF_u] = -self.f_gyr_u_el(t, q[elDOF], u[elDOF_u], el)
        return coo


def make_CosseratRodConstrained(mixed, constraints):
    if mixed == True:
        CosseratRodBase = CosseratRodMixed
        idx = np.arange(6)
        idx_constraints = np.array(constraints)
        idx_mixed = idx[~np.isin(idx, idx_constraints)]
    else:
        CosseratRodBase = CosseratRod
        idx_mixed = None

    class CosseratRodConstrained(CosseratRodBase):
        def __init__(
            self,
            cross_section,
            material_model,
            nelement,
            polynomial_degree,
            nquadrature,
            Q,
            q0=None,
            u0=None,
            nquadrature_dyn=None,
            cross_section_inertias=CrossSectionInertias(),
        ):
            super().__init__(
                cross_section,
                material_model,
                nelement,
                polynomial_degree,
                nquadrature,
                Q,
                q0=q0,
                u0=u0,
                nquadrature_dyn=nquadrature_dyn,
                cross_section_inertias=cross_section_inertias,
                idx_mixed=idx_mixed,
            )

            self.constraints = np.array(constraints)
            self.constraints_gamma = self.constraints[(self.constraints < 3)]
            self.nconstraints_gamma = len(self.constraints_gamma)
            self.K_n_la_g_sieve = np.zeros((3, self.nconstraints_gamma))
            for i, gammai in enumerate(self.constraints_gamma):
                self.K_n_la_g_sieve[gammai, i] = 1

            self.constraints_kappa = self.constraints[(self.constraints >= 3)] - 3
            self.nconstraints_kappa = len(self.constraints_kappa)
            self.K_m_la_g_sieve = np.zeros((3, self.nconstraints_kappa))
            for i, kappai in enumerate(self.constraints_kappa):
                self.K_m_la_g_sieve[kappai, i] = 1

            #######################################################
            # discretization parameters internal forces and moments
            #######################################################
            self.polynomial_degree_la_g = polynomial_degree - 1
            self.knot_vector_la_g = LagrangeKnotVector(
                self.polynomial_degree_la_g, nelement
            )

            # build mesh objects
            self.mesh_la_g = Mesh1D(
                self.knot_vector_la_g,
                nquadrature,
                dim_q=len(self.constraints),
                derivative_order=0,
                basis="Lagrange_Disc",
                quadrature="Gauss",
                dim_u=len(self.constraints),
            )

            # total number of nodes
            self.nnodes_la_g = self.mesh_la_g.nnodes

            # number of nodes per element
            self.nnodes_element_la_g = self.mesh_la_g.nnodes_per_element

            # total number of constraint coordinates
            self.nla_g = self.mesh_la_g.nq

            # number of compliance coordinates per element
            self.nla_g_element = self.mesh_la_g.nq_per_element

            # global element connectivity for copliance coordinates
            self.elDOF_la_g = self.mesh_la_g.elDOF

            # global nodal connectivity
            # TODO: Take care of self.nla_c for the mixed formulation
            self.nodalDOF_la_g = self.mesh_la_g.nodalDOF + self.nq_r + self.nq_p

            # nodal connectivity on element level
            self.nodalDOF_element_la_g = self.mesh_la_g.nodalDOF_element

            # shape functions
            self.N_la_g = self.mesh_la_g.N

            # TODO: Dummy initial values for compliance
            self.la_g0 = np.zeros(self.nla_g, dtype=float)

            # evaluate shape functions at specific xi
            self.basis_functions_la_g = self.mesh_la_g.eval_basis

        def eval_constraint_stresses(self, la_g, xi):
            el = self.element_number(xi)
            la_ge = la_g[self.elDOF_la_g[el]]
            # TODO: lets see how to avoid the flatten
            N_la_ge = self.basis_functions_la_g(xi).flatten()
            la_gg = np.zeros(self.nconstraints_gamma + self.nconstraints_kappa)

            for node in range(self.nnodes_element_la_g):
                la_g_node = la_ge[self.nodalDOF_element_la_g[node]]
                la_gg += N_la_ge[node] * la_g_node

            K_n_c = self.K_n_la_g_sieve @ la_gg[: self.nconstraints_gamma]
            K_m_c = self.K_m_la_g_sieve @ la_gg[self.nconstraints_gamma :]

            return K_n_c, K_m_c

        def g(self, t, q):
            g = np.zeros(self.nla_g, dtype=q.dtype)
            for el in range(self.nelement):
                elDOF = self.elDOF[el]
                elDOF_la_g = self.elDOF_la_g[el]
                g[elDOF_la_g] = self.g_el(q[elDOF], el)
            return g

        def g_el(self, qe, el):
            # TODO: check this after you have slept
            g_el = np.zeros(self.nla_g_element, dtype=qe.dtype)

            for i in range(self.nquadrature):
                # extract reference state variables
                qpi = self.qp[el, i]
                qwi = self.qw[el, i]
                J = self.J[el, i]
                K_Gamma0 = self.K_Gamma0[el, i]
                K_Kappa0 = self.K_Kappa0[el, i]

                # evaluate required quantities
                _, _, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

                # TODO: Store K_Gamma_bar0 and K_Kappa_bar0
                delta_strains = np.concatenate(
                    [
                        (K_Gamma_bar - J * K_Gamma0) * qwi,
                        (K_Kappa_bar - J * K_Kappa0) * qwi,
                    ]
                )

                for node in range(self.nnodes_element_la_g):
                    g_el[self.nodalDOF_element_la_g[node]] -= (
                        self.N_la_g[el, i, node] * delta_strains[self.constraints]
                    )

            return g_el

        def g_q(self, t, q):
            coo = CooMatrix((self.nla_g, self.nq))
            for el in range(self.nelement):
                elDOF = self.elDOF[el]
                elDOF_la_g = self.elDOF_la_g[el]
                coo[elDOF_la_g, elDOF] = self.g_q_el(q[elDOF], el)
            return coo

        def g_q_el(self, qe, el):
            if not hasattr(self, "_deval"):
                warnings.warn(
                    "Class derived from CosseratRod does not implement _deval. We use a numerical Jacobian!"
                )
                return approx_fprime(
                    qe, lambda qe: self.g_el(qe, el), method="3-point", eps=1e-6
                )

            g_q_el = np.zeros((self.nla_g_element, self.nq_element), dtype=qe.dtype)
            for i in range(self.nquadrature):
                # extract reference state variables
                qpi = self.qp[el, i]
                qwi = self.qw[el, i]
                J = self.J[el, i]

                # evaluate required quantities
                (
                    _,
                    _,
                    _,
                    _,
                    _,
                    _,
                    K_Gamma_bar_qe,
                    K_Kappa_bar_qe,
                ) = self._deval(qe, qpi)

                delta_strains_qe = np.vstack((K_Gamma_bar_qe, K_Kappa_bar_qe))

                for node in range(self.nnodes_element_la_g):
                    nodalDOF_la_g = self.nodalDOF_element_la_g[node]
                    g_q_el[nodalDOF_la_g, :] -= (
                        self.N_la_g[el, i, node]
                        * delta_strains_qe[self.constraints, :]
                        * qwi
                    )

            return g_q_el

        def W_g(self, t, q):
            coo = CooMatrix((self.nu, self.nla_g))
            for el in range(self.nelement):
                elDOF = self.elDOF[el]
                elDOF_u = self.elDOF_u[el]
                elDOF_la_g = self.elDOF_la_g[el]
                coo[elDOF_u, elDOF_la_g] = self.W_g_el(q[elDOF], el)
            return coo

        def W_g_el(self, qe, el):
            W_g_el = np.zeros((self.nu_element, self.nla_g_element), dtype=qe.dtype)

            for i in range(self.nquadrature):
                # extract reference state variables
                qpi = self.qp[el, i]
                qwi = self.qw[el, i]

                # evaluate required quantities
                _, A_IK, K_Gamma_bar, K_Kappa_bar = self._eval(qe, qpi)

                ############################
                # virtual work contributions
                ############################
                for node_r in range(self.nnodes_element_r):
                    nodalDOF_r = self.nodalDOF_element_r_u[node_r]
                    N_r_xi = self.N_r_xi[el, i, node_r]
                    for node_la_g in range(self.nnodes_element_la_g):
                        nodalDOF_la_g = self.nodalDOF_element_la_g[node_la_g]
                        W_g_el[
                            nodalDOF_r[:, None],
                            nodalDOF_la_g[: self.nconstraints_gamma],
                        ] -= (
                            N_r_xi
                            * A_IK[:, self.constraints_gamma]
                            * self.N_la_g[el, i, node_la_g]
                            * qwi
                        )

                for node_p in range(self.nnodes_element_p):
                    nodalDOF_p = self.nodalDOF_element_p_u[node_p]
                    N_p = self.N_p[el, i, node_p]
                    N_p_xi = self.N_p_xi[el, i, node_p]

                    for node_la_g in range(self.nnodes_element_la_g):
                        nodalDOF_la_g = self.nodalDOF_element_la_g[node_la_g]
                        W_g_el[
                            nodalDOF_p[:, None],
                            nodalDOF_la_g[self.nconstraints_gamma :],
                        ] -= (
                            (N_p_xi * np.eye(3) - N_p * ax2skew(K_Kappa_bar))[
                                :, self.constraints_kappa
                            ]
                            * self.N_la_g[el, i, node_la_g]
                            * qwi
                        )

                        W_g_el[
                            nodalDOF_p[:, None],
                            nodalDOF_la_g[: self.nconstraints_gamma],
                        ] += (
                            N_p
                            * ax2skew(K_Gamma_bar)[:, self.constraints_gamma]
                            * self.N_la_g[el, i, node_la_g]
                            * qwi
                        )

            return W_g_el

        def Wla_g_q(self, t, q, la_g):
            coo = CooMatrix((self.nu, self.nq))
            for el in range(self.nelement):
                elDOF = self.elDOF[el]
                elDOF_u = self.elDOF_u[el]
                elDOF_la_g = self.elDOF_la_g[el]
                coo[elDOF_u, elDOF] = self.Wla_g_q_el(q[elDOF], la_g[elDOF_la_g], el)
            return coo

        def Wla_g_q_el(self, qe, la_ge, el):
            if not hasattr(self, "_deval"):
                warnings.warn(
                    "Class derived from CosseratRod does not implement _deval. We use a numerical Jacobian!"
                )
                return approx_fprime(
                    qe,
                    lambda qe: self.W_g_el(qe, el) @ la_ge,
                    method="3-point",
                    eps=1e-6,
                )

            Wla_g_q_el = np.zeros(
                (self.nu_element, self.nq_element), dtype=np.common_type(qe, la_ge)
            )

            for i in range(self.nquadrature):
                # extract reference state variables
                qpi = self.qp[el, i]
                qwi = self.qw[el, i]
                J = self.J[el, i]

                # evaluate required quantities
                (
                    r_OP,
                    A_IK,
                    K_Gamma_bar,
                    K_Kappa_bar,
                    r_OP_qe,
                    A_IK_qe,
                    K_Gamma_bar_qe,
                    K_Kappa_bar_qe,
                ) = self._deval(qe, qpi)

                # interpolation of the n and m
                la_g = np.zeros(self.mesh_la_g.dim_q, dtype=qe.dtype)

                for node in range(self.nnodes_element_la_g):
                    la_g_node = la_ge[self.nodalDOF_element_la_g[node]]
                    la_g += self.N_la_g[el, i, node] * la_g_node

                K_n = self.K_n_la_g_sieve @ la_g[: self.nconstraints_gamma]
                K_m = self.K_m_la_g_sieve @ la_g[self.nconstraints_gamma :]

                for node in range(self.nnodes_element_r):
                    Wla_g_q_el[self.nodalDOF_element_r_u[node], :] -= (
                        self.N_r_xi[el, i, node]
                        * qwi
                        * (np.einsum("ikj,k->ij", A_IK_qe, K_n))
                    )

                for node in range(self.nnodes_element_p):
                    Wla_g_q_el[self.nodalDOF_element_p_u[node], :] += (
                        self.N_p[el, i, node]
                        * qwi
                        * (
                            -ax2skew(K_n) @ K_Gamma_bar_qe
                            - ax2skew(K_m) @ K_Kappa_bar_qe
                        )
                    )

            # Wla_g_q_el_num = approx_fprime(qe, lambda qe: self.W_g_el(qe, el) @ la_ge, method="cs", eps=1e-6)
            # error = np.linalg.norm(Wla_g_q_el - Wla_g_q_el_num)
            # print(error)

            return Wla_g_q_el

        def g_dot(self, t, q, u):
            raise NotImplementedError
            return self.W_g(t, q).toarray.T @ u

        def g_dot_u(self, t, q):
            W_g = self.W_g(t, q)
            coo = CooMatrix((self.nla_g, self.nu))
            coo.row = W_g.col
            coo.col = W_g.row
            coo.data = W_g.data
            return coo

        def g_dot_q(self, t, q, u):
            raise NotImplementedError

        def g_ddot(self, t, q, u):
            raise NotImplementedError

    return CosseratRodConstrained
