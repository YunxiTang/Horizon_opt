#include <casadi/casadi.hpp>
#include <Eigen/Dense>
#include <memory>

#include "profiling.h"

namespace horizon
{

typedef Eigen::Ref<const Eigen::VectorXd> VecConstRef;
typedef Eigen::Ref<const Eigen::MatrixXd> MatConstRef;

typedef std::function<bool(const Eigen::MatrixXd& xtrj,
                           const Eigen::MatrixXd& utrj,
                           double step_length,
                           double total_cost,
                           double defect_norm,
                           double constraint_violation)> CallbackType;

/**
 * @brief IterativeLQR implements a multiple-shooting variant of the
 * notorious ILQR algorithm, implemented following the paper
 * "A Family of Iterative Gauss-Newton Shooting Methods for Nonlinear
 * Optimal Control" by M. Giftthaler, et al., from which most of the notation
 * is taken.
 *
 * The framework supports arbitrary (differentiable) discrete time dynamics
 * systems as well as arbitrary (twice differentiable) cost functions.
 *
 * Furthermore, arbitrary (differentiable) equality constraints are treated
 * with a projection approach.
 */
class IterativeLQR
{

public:


     /**
     * @brief Class constructor
     * @param fdyn is a function mapping state and control to the integrated state;
     * required signature is (x, u) -> (f)
     * @param N is the number of shooting intervals
     */
    IterativeLQR(casadi::Function fdyn,
                 int N);

    /**
     * @brief set an intermediate cost term for each intermediate state
     * @param inter_cost: a vector of N entries, each of which is a function with
     * required signature (x, u) -> (l)
     */
    void setIntermediateCost(const std::vector<casadi::Function>& inter_cost);

    /**
     * @brief set an intermediate cost term for the k-th intermediate state
     * @param k: the node that the cost refers to
     * @param inter_cost: a function with required signature (x, u) -> (l)
     */
    void setIntermediateCost(int k, const casadi::Function& inter_cost);

    /**
     * @brief set the final cost
     * @param final_cost: a function with required signature (x, u) -> (l),
     * even though the input 'u' is not used
     */
    void setFinalCost(const casadi::Function& final_cost);

    /**
     * @brief  set an intermediate constraint term for the k-th intermediate state
     * @param k: the node that the cost refers to
     * @param inter_constraint: a function with required signature (x, u) -> (h),
     * where the constraint is h(x, u) = 0
     */
    void setIntermediateConstraint(int k, const casadi::Function& inter_constraint);

    void setIntermediateConstraint(const std::vector<casadi::Function>& inter_constraint);

    void setFinalConstraint(const casadi::Function& final_constraint);

    void setInitialState(const Eigen::VectorXd& x0);

    void setIterationCallback(const CallbackType& cb);

    bool solve(int max_iter);

    const Eigen::MatrixXd& getStateTrajectory() const;

    const Eigen::MatrixXd& getInputTrajectory() const;

    const utils::ProfilingInfo& getProfilingInfo() const;

    VecConstRef state(int i) const;

    VecConstRef input(int i) const;

    ~IterativeLQR();

protected:

private:

    struct ConstrainedDynamics;
    struct ConstrainedCost;
    struct Dynamics;
    struct Constraint;
    struct IntermediateCost;
    struct Temporaries;
    struct ConstraintToGo;
    struct BackwardPassResult;
    struct ForwardPassResult;
    struct ValueFunction;

    typedef std::tuple<int, ConstrainedDynamics, ConstrainedCost> HandleConstraintsRetType;

    void linearize_quadratize();
    void report_result();
    void backward_pass();
    void backward_pass_iter(int i);
    HandleConstraintsRetType handle_constraints(int i);
    bool forward_pass(double alpha);
    void forward_pass_iter(int i, double alpha);
    void set_default_cost();

    int _nx;
    int _nu;
    int _N;

    std::vector<IntermediateCost> _cost;
    std::vector<Constraint> _constraint;
    std::vector<ValueFunction> _value;
    std::vector<Dynamics> _dyn;

    std::vector<BackwardPassResult> _bp_res;
    std::unique_ptr<ConstraintToGo> _constraint_to_go;
    std::unique_ptr<ForwardPassResult> _fp_res;

    Eigen::MatrixXd _xtrj;
    Eigen::MatrixXd _utrj;

    std::vector<Temporaries> _tmp;

    CallbackType _iter_cb;
    utils::ProfilingInfo _prof_info;
};



}