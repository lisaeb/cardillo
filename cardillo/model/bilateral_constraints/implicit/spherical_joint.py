import numpy as np

class Spherical_joint():
    def __init__(self, subsystem1, subsystem2, r_OB, frame_ID1=np.zeros(3), frame_ID2=np.zeros(3), la_g0=None):
        self.nla_g = 3
        self.la_g0 = np.zeros(self.nla_g) if la_g0 is None else la_g0

        self.subsystem1 = subsystem1
        self.frame_ID1 = frame_ID1
        self.subsystem2 = subsystem2
        self.frame_ID2 = frame_ID2
        self.r_OB = r_OB
        
    def assembler_callback(self):
        qDOF1 = self.subsystem1.qDOF_P(self.frame_ID1)
        qDOF2 = self.subsystem2.qDOF_P(self.frame_ID2)
        self.qDOF = np.concatenate([self.subsystem1.qDOF[qDOF1], self.subsystem2.qDOF[qDOF2]])
        self.nq1 = nq1 = len(qDOF1)
        self.nq2 = len(qDOF2)
        self.__nq = self.nq1 + self.nq2
        
        uDOF1 = self.subsystem1.uDOF_P(self.frame_ID1)
        uDOF2 = self.subsystem2.uDOF_P(self.frame_ID2)
        self.uDOF = np.concatenate([self.subsystem1.uDOF[uDOF1], self.subsystem2.uDOF[uDOF2]])
        self.nu1 = nu1 = len(uDOF1)
        self.nu2 = len(uDOF2)
        self.__nu = self.nu1 + self.nu2

        r_OS1 = self.subsystem1.r_OP(self.subsystem1.t0, self.subsystem1.q0[qDOF1], self.frame_ID1)
        if hasattr(self.subsystem1, 'A_IK'):
            A_IK1 = self.subsystem1.A_IK(self.subsystem1.t0, self.subsystem1.q0[qDOF1], self.frame_ID1)
            K_r_SP1 = A_IK1.T @ (self.r_OB - r_OS1)
        else:
            K_r_SP1 = np.zeros(3)

        r_OS2 = self.subsystem2.r_OP(self.subsystem2.t0, self.subsystem2.q0[qDOF2], self.frame_ID2)
        if hasattr(self.subsystem2, 'A_IK'):
            A_IK2 = self.subsystem2.A_IK(self.subsystem2.t0, self.subsystem2.q0[qDOF2], self.frame_ID2)
            K_r_SP2 = A_IK2.T @ (self.r_OB - r_OS2)
        else:
            K_r_SP2 = np.zeros(3)

        self.r_OP1 = lambda t, q: self.subsystem1.r_OP(t, q[:nq1], self.frame_ID1, K_r_SP1)
        self.r_OP1_q = lambda t, q: self.subsystem1.r_OP_q(t, q[:nq1], self.frame_ID1, K_r_SP1)
        self.v_P1 = lambda t, q, u: self.subsystem1.v_P(t, q[:nq1], u[:nu1], self.frame_ID1, K_r_SP1)
        self.a_P1 = lambda t, q, u, u_dot: self.subsystem1.a_P(t, q[:nq1], u[:nu1], u_dot[:nu1], self.frame_ID1, K_r_SP1)
        self.J_P1 = lambda t, q: self.subsystem1.J_P(t, q[:nq1], self.frame_ID1, K_r_SP1)
        self.J_P1_q = lambda t, q: self.subsystem1.J_P_q(t, q[:nq1], self.frame_ID1, K_r_SP1)

        self.r_OP2 = lambda t, q: self.subsystem2.r_OP(t, q[nq1:], self.frame_ID2, K_r_SP2)
        self.r_OP2_q = lambda t, q: self.subsystem2.r_OP_q(t, q[nq1:], self.frame_ID2, K_r_SP2)
        self.v_P2 = lambda t, q, u: self.subsystem2.v_P(t, q[nq1:], u[nu1:], self.frame_ID2, K_r_SP2)
        self.a_P2 = lambda t, q, u, u_dot: self.subsystem2.a_P(t, q[nq1:], u[nu1:], u_dot[nu1:], self.frame_ID2, K_r_SP2)
        self.J_P2 = lambda t, q: self.subsystem2.J_P(t, q[nq1:], self.frame_ID2, K_r_SP2)
        self.J_P2_q = lambda t, q: self.subsystem2.J_P_q(t, q[nq1:], self.frame_ID2, K_r_SP2)
        
    def g(self, t, q):
        r_OP1 = self.r_OP1(t, q)
        r_OP2 = self.r_OP2(t, q)
        return r_OP2 - r_OP1

    def g_q_dense(self, t, q):
        r_OP1_q = self.r_OP1_q(t, q) 
        r_OP2_q = self.r_OP2_q(t, q)
        return np.hstack([-r_OP1_q, r_OP2_q])

    def g_dot(self, t, q, u):
        v_P1 = self.v_P1(t, q, u) 
        v_P2 = self.v_P2(t, q, u)
        return v_P2 - v_P1

    def g_dot_u(self, t, q, coo):
        coo.extend(self.W_g_dense(t, q).T, (self.la_gDOF, self.uDOF))

    def g_ddot(self, t, q, u, u_dot):
        a_P1 = self.a_P1(t, q, u, u_dot) 
        a_P2 = self.a_P2(t, q, u, u_dot)
        return a_P2 - a_P1

    def g_q(self, t, q, coo):
        coo.extend(self.g_q_dense(t, q), (self.la_gDOF, self.qDOF))
   
    def W_g_dense(self, t, q):
        nu1 = self.nu1
        J_P1 = self.J_P1(t, q) 
        J_P2 = self.J_P2(t, q)
        W_g = np.zeros((self.__nu, self.nla_g))
        W_g[:nu1, :] = -J_P1.T
        W_g[nu1:, :] = J_P2.T
        return W_g
        
    def W_g(self, t, q, coo):
        coo.extend(self.W_g_dense(t, q), (self.uDOF, self.la_gDOF))

    def Wla_g_q(self, t, q, la_g, coo):
        nq1 = self.nq1
        nu1 = self.nu1
        J_P1_q = self.J_P1_q(t, q) 
        J_P2_q = self.J_P2_q(t, q)

        # dense blocks
        dense = np.zeros((self.__nu, self.__nq))
        dense[:nu1, :nq1] = np.einsum('i,ijk->jk', -la_g, J_P1_q)
        dense[nu1:, nq1:] = np.einsum('i,ijk->jk', la_g, J_P2_q)

        coo.extend( dense, (self.uDOF, self.qDOF))

class Spherical_joint2D(Spherical_joint):
    def __init__(self, subsystem1, subsystem2, r_OB, frame_ID1=np.zeros(3), frame_ID2=np.zeros(3), la_g0=None):
        super().__init__(subsystem1, subsystem2, r_OB, frame_ID1=frame_ID1, frame_ID2=frame_ID2, la_g0=None)
        self.nla_g = 2
        self.la_g0 = np.zeros(self.nla_g) if la_g0 is None else la_g0

    def g(self, t, q):
        return super().g(t, q)[:2]

    def g_q_dense(self, t, q):
        return super().g_q_dense(t, q)[:2]

    def g_dot(self, t, q, u):
        return super().g_dot(t, q, u)[:2]

    def g_dot_u(self, t, q, coo):
        coo.extend(self.W_g_dense(t, q).T[:2], (self.la_gDOF, self.uDOF))

    def g_ddot(self, t, q, u, u_dot):
        return super().g_ddot(t, q, u, u_dot)[:2]

    def g_q(self, t, q, coo):
        coo.extend(self.g_q_dense(t, q)[:2], (self.la_gDOF, self.qDOF))
   
    def W_g_dense(self, t, q):
        nu1 = self.nu1
        J_P1 = self.J_P1(t, q) 
        J_P2 = self.J_P2(t, q)
        W_g = np.zeros((self.__nu, self.nla_g))
        W_g[:nu1, :] = -J_P1[:2].T
        W_g[nu1:, :] = J_P2[:2].T
        return W_g
        
    def W_g(self, t, q, coo):
        coo.extend(self.W_g_dense(t, q), (self.uDOF, self.la_gDOF))

    def Wla_g_q(self, t, q, la_g, coo):
        nq1 = self.nq1
        nu1 = self.nu1
        J_P1_q = self.J_P1_q(t, q) 
        J_P2_q = self.J_P2_q(t, q)

        # dense blocks
        dense = np.zeros((self.__nu, self.__nq))
        dense[:nu1, :nq1] = np.einsum('i,ijk->jk', -la_g, J_P1_q[:2])
        dense[nu1:, nq1:] = np.einsum('i,ijk->jk', la_g, J_P2_q[:2])

        coo.extend( dense, (self.uDOF, self.qDOF))